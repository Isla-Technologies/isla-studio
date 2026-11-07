use anyhow::Result;
use clap::{Args, Parser, Subcommand};
use isla_server::{
    app::{self, Platform},
    paths::Paths,
};
use std::{io::Read, path::PathBuf};

#[derive(Parser)]
#[command(
    name = "isla-studio",
    version,
    about = "Isla Studio native Rust service and training tools"
)]
struct Cli {
    #[command(subcommand)]
    command: Option<Operation>,
    #[command(flatten)]
    server: Serve,
}
#[derive(Args, Default)]
struct Serve {
    #[arg(long, default_value = "127.0.0.1")]
    host: String,
    #[arg(long, default_value_t = 8848)]
    port: u16,
    #[arg(long)]
    robot: Option<String>,
    #[arg(long)]
    no_browser: bool,
    #[arg(long)]
    stop_with_stdin: bool,
}
#[derive(Subcommand)]
enum Operation {
    Serve(Serve),
    Train {
        folder: PathBuf,
        #[arg(long, default_value = "auto")]
        device: String,
        #[arg(long)]
        iterations: Option<usize>,
        #[arg(long)]
        threads: Option<usize>,
        #[arg(long)]
        no_swarm: bool,
    },
    Evaluate {
        folder: PathBuf,
    },
    Runtime {
        #[arg(long, default_value = "luma_1")]
        robot: String,
        #[arg(long, default_value = "0.0.0.0")]
        host: String,
        #[arg(long, default_value_t = 47210)]
        port: u16,
        #[arg(long)]
        settings: Option<PathBuf>,
    },
    /// Verify native physics, model assets and shipped policy formats without a server.
    Check,
}

#[tokio::main]
async fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        None => serve(cli.server).await,
        Some(Operation::Serve(options)) => serve(options).await,
        Some(Operation::Train {
            folder,
            device,
            iterations,
            threads,
            no_swarm,
        }) => {
            isla_server::runs::validate_device(&device)?;
            let _ = (folder, iterations, threads, no_swarm);
            anyhow::bail!("The native training environment is not complete. Use the reference Python trainer until training parity is established.")
        }
        Some(Operation::Evaluate { folder }) => {
            let _ = folder;
            anyhow::bail!(
                "The native evaluator is not complete. Use the reference Python evaluator."
            )
        }
        Some(Operation::Runtime {
            robot,
            host,
            port,
            settings,
        }) => {
            let paths = Paths::discover()?;
            paths.ensure()?;
            let profile = isla_sim::RobotProfile::load(&paths.robots, &robot)?;
            let settings = if let Some(file) = settings {
                isla_hardware::HardwareSettings::from_value(isla_server::paths::read_json(&file)?)?
            } else {
                isla_hardware::HardwareSettings::load(&paths.settings, &robot, &profile.data)?
            };
            let mut driver = isla_hardware::link::make_driver(&profile, &settings)?;
            driver.connect()?;
            let (shutdown, receiver) = tokio::sync::watch::channel(false);
            tokio::spawn(async move {
                let _ = tokio::signal::ctrl_c().await;
                let _ = shutdown.send(true);
            });
            isla_hardware::relay::serve(
                driver,
                &host,
                port,
                std::time::Duration::from_secs_f64(settings.limits.state_timeout),
                receiver,
            )
            .await
        }
        Some(Operation::Check) => {
            let paths = Paths::discover()?;
            let profile = isla_sim::RobotProfile::load(&paths.root.join("robots"), "luma_1")?;
            let mut session = isla_sim::LiveSession::new(profile, None)?;
            for _ in 0..100 {
                session.step()?;
            }
            for name in ["luma1-walk", "luma1-leg-design"] {
                isla_learn::Policy::load(
                    &paths
                        .root
                        .join("examples/runs")
                        .join(name)
                        .join("policy.json"),
                )?;
            }
            println!("Native MuJoCo, Luma-1 simulation and both shipped policy files: OK");
            Ok(())
        }
    }
}

async fn serve(options: Serve) -> Result<()> {
    let state = Platform::new(Paths::discover()?, options.robot.as_deref())?;
    // Hold the listener before publishing readiness: port 0 is selected without a bind race.
    let listener = tokio::net::TcpListener::bind((options.host.as_str(), options.port)).await?;
    let address = listener.local_addr()?;
    let host = match options.host.as_str() {
        "0.0.0.0" | "127.0.0.1" => "localhost",
        other => other,
    };
    let host = if host.contains(':') {
        format!("[{host}]")
    } else {
        host.to_owned()
    };
    let url = format!("http://{host}:{}", address.port());
    let (stop_tx, stop_rx) = tokio::sync::oneshot::channel();
    if options.stop_with_stdin {
        std::thread::spawn(move || {
            let mut buffer = [0u8; 4096];
            let mut input = std::io::stdin().lock();
            while matches!(input.read(&mut buffer),Ok(n) if n>0) {}
            let _ = stop_tx.send(());
        });
    } else {
        // Keep this sender alive while the Ctrl-C branch waits.
        tokio::spawn(async move {
            let _ = tokio::signal::ctrl_c().await;
            let _ = stop_tx.send(());
        });
    }
    state.start();
    println!("Isla Studio (Rust): {url}");
    if !options.no_browser {
        let _ = webbrowser::open(&url);
    }
    let router = app::router(state.clone());
    let shutdown_state = state.clone();
    let result = axum::serve(listener, router)
        .with_graceful_shutdown(async move {
            tokio::select! {_=stop_rx=>{},_=tokio::signal::ctrl_c()=>{}}
            let _ = tokio::task::spawn_blocking(move || shutdown_state.close()).await;
        })
        .await;
    state.close();
    result?;
    Ok(())
}
