use anyhow::{ensure, Result};
use ndarray::{Array1, Array2, ArrayD, Axis};
use rand::Rng;
use rand_distr::StandardNormal;

#[derive(Debug, Clone)]
pub struct Mlp {
    pub weights: Vec<Array2<f32>>,
    pub biases: Vec<Array1<f32>>,
}

impl Mlp {
    pub fn new(sizes: &[usize], output_gain: f64, rng: &mut impl Rng) -> Result<Self> {
        ensure!(
            sizes.len() >= 2 && sizes.iter().all(|&n| n > 0),
            "A network needs positive input and output sizes"
        );
        let mut weights = Vec::new();
        let mut biases = Vec::new();
        for (i, pair) in sizes.windows(2).enumerate() {
            let (rows, cols) = (pair[0], pair[1]);
            let gain = if i + 2 == sizes.len() {
                output_gain
            } else {
                2.0_f64.sqrt()
            };
            // Modified Gram-Schmidt with reorthogonalisation. Tall matrices have
            // orthonormal columns; wide matrices are generated transposed.
            let (m, n) = (rows.max(cols), rows.min(cols));
            let mut q = Array2::<f64>::from_shape_fn((m, n), |_| rng.sample(StandardNormal));
            for j in 0..n {
                for _ in 0..2 {
                    for k in 0..j {
                        let dot = q.column(j).dot(&q.column(k));
                        for r in 0..m {
                            q[[r, j]] -= dot * q[[r, k]];
                        }
                    }
                }
                let norm = q.column(j).dot(&q.column(j)).sqrt();
                ensure!(norm > 1e-12, "Degenerate network initialization");
                for r in 0..m {
                    q[[r, j]] /= norm;
                }
            }
            let w = Array2::from_shape_fn((rows, cols), |(r, c)| {
                (gain * if rows < cols { q[[c, r]] } else { q[[r, c]] }) as f32
            });
            weights.push(w);
            biases.push(Array1::zeros(cols));
        }
        Ok(Self { weights, biases })
    }

    pub fn validate(&self) -> Result<()> {
        ensure!(
            !self.weights.is_empty() && self.weights.len() == self.biases.len(),
            "Missing network layers"
        );
        for (i, (w, b)) in self.weights.iter().zip(&self.biases).enumerate() {
            ensure!(
                w.nrows() > 0 && w.ncols() > 0 && b.len() == w.ncols(),
                "Invalid layer {i} shape"
            );
            ensure!(
                i == 0 || self.weights[i - 1].ncols() == w.nrows(),
                "Layer {i} does not follow its input"
            );
            ensure!(
                w.iter().chain(b.iter()).all(|v| v.is_finite()),
                "Non-finite network parameter"
            );
        }
        Ok(())
    }

    pub fn sizes(&self) -> Vec<usize> {
        let mut out = vec![self.weights[0].nrows()];
        out.extend(self.weights.iter().map(|w| w.ncols()));
        out
    }

    pub fn forward(&self, input: &Array2<f32>) -> Result<(Array2<f32>, Vec<Array2<f32>>)> {
        ensure!(
            input.ncols() == self.weights[0].nrows(),
            "Network input width mismatch"
        );
        let mut x = input.clone();
        let mut inputs = Vec::with_capacity(self.weights.len());
        for (i, (w, b)) in self.weights.iter().zip(&self.biases).enumerate() {
            inputs.push(x.clone());
            x = x.dot(w) + b;
            if i + 1 < self.weights.len() {
                x.mapv_inplace(|v| if v > 0.0 { v } else { v.exp() - 1.0 });
            }
        }
        Ok((x, inputs))
    }

    pub fn predict(&self, input: &Array2<f32>) -> Result<Array2<f32>> {
        Ok(self.forward(input)?.0)
    }

    pub fn backward(
        &self,
        gradient: &Array2<f32>,
        inputs: &[Array2<f32>],
    ) -> Result<Vec<ArrayD<f32>>> {
        ensure!(
            inputs.len() == self.weights.len(),
            "Network backward input count mismatch"
        );
        let mut grad = gradient.clone();
        let mut result = Vec::with_capacity(2 * self.weights.len());
        for layer in (0..self.weights.len()).rev() {
            let x = &inputs[layer];
            ensure!(
                grad.dim() == (x.nrows(), self.weights[layer].ncols()),
                "Gradient shape mismatch"
            );
            result.push(grad.sum_axis(Axis(0)).into_dyn());
            result.push(x.t().dot(&grad).into_dyn());
            if layer > 0 {
                grad = grad.dot(&self.weights[layer].t());
                grad *= &x.mapv(|v| (v + 1.0).min(1.0));
            }
        }
        result.reverse();
        Ok(result)
    }

    pub fn params(&self) -> Vec<ArrayD<f32>> {
        self.weights
            .iter()
            .zip(&self.biases)
            .flat_map(|(w, b)| [w.clone().into_dyn(), b.clone().into_dyn()])
            .collect()
    }

    pub fn set_params(&mut self, params: &[ArrayD<f32>]) -> Result<()> {
        ensure!(
            params.len() == self.weights.len() * 2,
            "Parameter count mismatch"
        );
        for (i, (w, b)) in self
            .weights
            .iter_mut()
            .zip(self.biases.iter_mut())
            .enumerate()
        {
            ensure!(
                params[2 * i].shape() == w.shape() && params[2 * i + 1].shape() == b.shape(),
                "Parameter shape mismatch"
            );
            w.assign(&params[2 * i].view().into_dimensionality::<ndarray::Ix2>()?);
            b.assign(
                &params[2 * i + 1]
                    .view()
                    .into_dimensionality::<ndarray::Ix1>()?,
            );
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct Normalizer {
    pub mean: Array1<f64>,
    pub var: Array1<f64>,
    pub count: f64,
    pub clip: f64,
}

impl Normalizer {
    pub fn new(size: usize) -> Self {
        Self {
            mean: Array1::zeros(size),
            var: Array1::ones(size),
            count: 0.0,
            clip: 10.0,
        }
    }
    pub fn update(&mut self, batch: &Array2<f32>) -> Result<()> {
        ensure!(
            batch.ncols() == self.mean.len(),
            "Normalizer width mismatch"
        );
        ensure!(
            batch.iter().all(|x| x.is_finite()),
            "Non-finite observation"
        );
        if batch.nrows() == 0 {
            return Ok(());
        }
        let n = batch.nrows() as f64;
        let values = batch.mapv(f64::from);
        let mean = values.sum_axis(Axis(0)) / n;
        let var = (&values - &mean).mapv(|x| x * x).sum_axis(Axis(0)) / n;
        let total = self.count + n;
        let delta = &mean - &self.mean;
        self.mean += &(&delta * (n / total));
        self.var =
            (&self.var * self.count + var * n + delta.mapv(|x| x * x) * (self.count * n / total))
                / total;
        self.count = total;
        Ok(())
    }
    pub fn std(&self) -> Array1<f64> {
        self.var.mapv(|v| (v + 1e-8).sqrt())
    }
    pub fn normalize(&self, batch: &Array2<f32>) -> Result<Array2<f32>> {
        ensure!(
            batch.ncols() == self.mean.len(),
            "Normalizer width mismatch"
        );
        Ok(((batch.mapv(f64::from) - &self.mean) / self.std())
            .mapv(|v| v.clamp(-self.clip, self.clip) as f32))
    }
}

#[derive(Debug, Clone)]
pub struct Adam {
    pub first: Vec<ArrayD<f32>>,
    pub second: Vec<ArrayD<f32>>,
    pub steps: u64,
    pub betas: (f64, f64),
    pub eps: f64,
}
impl Adam {
    pub fn new(params: &[ArrayD<f32>]) -> Self {
        Self {
            first: params.iter().map(|p| ArrayD::zeros(p.raw_dim())).collect(),
            second: params.iter().map(|p| ArrayD::zeros(p.raw_dim())).collect(),
            steps: 0,
            betas: (0.9, 0.999),
            eps: 1e-8,
        }
    }
    pub fn step(
        &mut self,
        params: &mut [ArrayD<f32>],
        grads: &[ArrayD<f32>],
        rate: f64,
    ) -> Result<()> {
        ensure!(
            params.len() == grads.len() && params.len() == self.first.len(),
            "Optimizer parameter count mismatch"
        );
        ensure!(rate.is_finite() && rate > 0.0, "Invalid learning rate");
        for (p, g) in params.iter().zip(grads) {
            ensure!(
                p.shape() == g.shape() && g.iter().all(|v| v.is_finite()),
                "Invalid optimizer gradient"
            );
        }
        self.steps += 1;
        let (b1, b2) = self.betas;
        let scale =
            rate * (1.0 - b2.powf(self.steps as f64)).sqrt() / (1.0 - b1.powf(self.steps as f64));
        for (((p, g), m), v) in params
            .iter_mut()
            .zip(grads)
            .zip(&mut self.first)
            .zip(&mut self.second)
        {
            ensure!(
                p.shape() == m.shape() && p.shape() == v.shape(),
                "Optimizer state shape mismatch"
            );
            for (((p, &g), m), v) in p
                .iter_mut()
                .zip(g.iter())
                .zip(m.iter_mut())
                .zip(v.iter_mut())
            {
                // Preserve float32 moment boundaries of the reference implementation.
                *m = (*m * b1 as f32) + (1.0 - b1) as f32 * g;
                *v = (*v * b2 as f32) + (1.0 - b2) as f32 * g * g;
                *p -= (scale as f32 * *m) / ((*v).sqrt() + self.eps as f32);
            }
        }
        Ok(())
    }
}
pub fn clip_norm(grads: &mut [ArrayD<f32>], limit: f64) -> f64 {
    let norm = grads
        .iter()
        .flat_map(|g| g.iter())
        .map(|&v| (v as f64).powi(2))
        .sum::<f64>()
        .sqrt();
    if norm > limit && limit > 0.0 {
        let scale = (limit / (norm + 1e-6)) as f32;
        for g in grads {
            *g *= scale;
        }
    }
    norm
}
