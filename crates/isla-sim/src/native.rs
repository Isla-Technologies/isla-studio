//! Owned MuJoCo handles. The library is kept alive until its last model/data is dropped.
use crate::ffi;
use anyhow::{anyhow, bail, Context, Result};
use std::{
    ffi::{CStr, CString},
    path::{Path, PathBuf},
    ptr::NonNull,
    sync::{Arc, OnceLock},
};

static LIBRARY: OnceLock<Arc<ffi::MjLib>> = OnceLock::new();
// This is an immutable table of symbols. Each simulation owns its model/data;
// no Studio code writes MuJoCo's global callback/allocator pointers.
unsafe impl Send for ffi::MjLib {}
unsafe impl Sync for ffi::MjLib {}
pub fn library() -> Result<Arc<ffi::MjLib>> {
    if let Some(api) = LIBRARY.get() {
        return Ok(api.clone());
    }
    let file = if cfg!(windows) {
        "mujoco.dll"
    } else if cfg!(target_os = "macos") {
        "libmujoco.3.15.0.dylib"
    } else {
        "libmujoco.so.3.15.0"
    };
    let mut candidates = Vec::new();
    if let Some(path) = std::env::var_os("ISLA_MUJOCO_LIBRARY") {
        candidates.push(PathBuf::from(path));
    }
    if let Some(path) = std::env::var_os("MUJOCO_DIR") {
        let root = PathBuf::from(path);
        candidates.extend([
            root.join(file),
            root.join("bin").join(file),
            root.join("lib").join(file),
        ]);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(root) = exe.parent() {
            candidates.extend([root.join(file), root.join("lib").join(file)]);
        }
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    candidates.extend([
        root.join("native/mujoco").join(file),
        root.join(".venv/Lib/site-packages/mujoco").join(file),
        PathBuf::from(file),
    ]);
    let mut errors = Vec::new();
    for path in candidates {
        let api = match unsafe { ffi::MjLib::new(&path) } {
            Ok(api) => api,
            Err(e) => {
                errors.push(format!("{}: {e}", path.display()));
                continue;
            }
        };
        let version = unsafe { api.mj_version() };
        if version != ffi::mjVERSION_HEADER as i32 {
            bail!(
                "MuJoCo version mismatch at {}: expected 3.15.0 ({}), found {version}",
                path.display(),
                ffi::mjVERSION_HEADER
            );
        }
        let api = Arc::new(api);
        let _ = LIBRARY.set(api.clone());
        return Ok(LIBRARY.get().cloned().unwrap_or(api));
    }
    bail!(
        "Cannot load native MuJoCo 3.15.0. Set ISLA_MUJOCO_LIBRARY to its library file. {}",
        errors.join("; ")
    )
}
pub fn cstring(text: &str) -> Result<CString> {
    CString::new(text).context("Text contains a NUL byte")
}
fn native_path(path: &Path) -> String {
    let path = path.to_string_lossy();
    if let Some(unc) = path.strip_prefix(r"\\?\UNC\") {
        format!(r"\\{unc}")
    } else {
        path.strip_prefix(r"\\?\").unwrap_or(&path).to_owned()
    }
}
pub unsafe fn text(ptr: *const std::ffi::c_char) -> String {
    if ptr.is_null() {
        String::new()
    } else {
        CStr::from_ptr(ptr).to_string_lossy().into_owned()
    }
}
pub unsafe fn slice<'a, T>(ptr: *const T, len: usize) -> &'a [T] {
    if len == 0 {
        &[]
    } else {
        std::slice::from_raw_parts(ptr, len)
    }
}
pub unsafe fn slice_mut<'a, T>(ptr: *mut T, len: usize) -> &'a mut [T] {
    if len == 0 {
        &mut []
    } else {
        std::slice::from_raw_parts_mut(ptr, len)
    }
}
pub unsafe fn v3(ptr: *const f64, index: usize) -> nalgebra::Vector3<f64> {
    let p = slice(ptr.add(index * 3), 3);
    nalgebra::Vector3::new(p[0], p[1], p[2])
}
pub unsafe fn a3(ptr: *const f64, index: usize) -> [f64; 3] {
    slice(ptr.add(index * 3), 3).try_into().unwrap()
}
pub unsafe fn a4(ptr: *const f64, index: usize) -> [f64; 4] {
    slice(ptr.add(index * 4), 4).try_into().unwrap()
}

pub struct Spec {
    pub(crate) ptr: NonNull<ffi::mjSpec>,
    pub(crate) api: Arc<ffi::MjLib>,
}
unsafe impl Send for Spec {}
impl Spec {
    pub fn load(path: &Path) -> Result<Self> {
        let api = library()?;
        let name = cstring(&native_path(path))?;
        let mut error = vec![0i8; 4096];
        let ptr = unsafe {
            api.mj_parseXML(
                name.as_ptr(),
                std::ptr::null(),
                error.as_mut_ptr(),
                error.len() as i32,
            )
        };
        Ok(Self {
            ptr: NonNull::new(ptr).ok_or_else(|| {
                anyhow!("MuJoCo cannot parse {}: {}", path.display(), unsafe {
                    text(error.as_ptr())
                })
            })?,
            api,
        })
    }
    pub fn from_xml(xml: &str) -> Result<Self> {
        let api = library()?;
        let xml = cstring(xml)?;
        let mut error = vec![0i8; 4096];
        let ptr = unsafe {
            api.mj_parseXMLString(xml.as_ptr(), std::ptr::null(), error.as_mut_ptr(), 4096)
        };
        Ok(Self {
            ptr: NonNull::new(ptr)
                .ok_or_else(|| anyhow!("MuJoCo XML: {}", unsafe { text(error.as_ptr()) }))?,
            api,
        })
    }
    pub fn raw(&self) -> &ffi::mjSpec {
        unsafe { self.ptr.as_ref() }
    }
    pub fn raw_mut(&mut self) -> &mut ffi::mjSpec {
        unsafe { self.ptr.as_mut() }
    }
    pub fn compile(&self) -> Result<Model> {
        let ptr = unsafe { self.api.mj_compile(self.ptr.as_ptr(), std::ptr::null()) };
        Model::from_raw(ptr, self.api.clone())
            .with_context(|| unsafe { text(self.api.mjs_getError(self.ptr.as_ptr())) })
    }
    pub fn elements(&self, kind: i32) -> Vec<*mut ffi::mjsElement> {
        unsafe {
            let mut e = self.api.mjs_firstElement(self.ptr.as_ptr(), kind);
            let mut out = Vec::new();
            while !e.is_null() {
                out.push(e);
                e = self.api.mjs_nextElement(self.ptr.as_ptr(), e);
            }
            out
        }
    }
    pub fn find(&self, kind: i32, name: &str) -> Result<*mut ffi::mjsElement> {
        let name = cstring(name)?;
        let e = unsafe {
            self.api
                .mjs_findElement(self.ptr.as_ptr(), kind, name.as_ptr())
        };
        if e.is_null() {
            bail!(
                "Model has no element {} of type {kind}",
                name.to_string_lossy()
            )
        }
        Ok(e)
    }
    pub fn body(&self, name: &str) -> Result<*mut ffi::mjsBody> {
        Ok(unsafe {
            self.api
                .mjs_asBody(self.find(ffi::mjtObj_mjOBJ_BODY, name)?)
        })
    }
    /// # Safety
    /// `e` must be a live element belonging to this spec.
    pub unsafe fn name(&self, e: *mut ffi::mjsElement) -> String {
        unsafe { text(self.api.mjs_getString(self.api.mjs_getName(e))) }
    }
    /// # Safety
    /// `e` must be a live element belonging to this spec.
    pub unsafe fn set_name(&self, e: *mut ffi::mjsElement, name: &str) -> Result<()> {
        let n = cstring(name)?;
        unsafe {
            self.api.mjs_setName(e, n.as_ptr());
        }
        Ok(())
    }
    /// # Safety
    /// `dest` must point to a live string field belonging to this spec.
    pub unsafe fn set_string(&self, dest: *mut ffi::mjString, value: &str) -> Result<()> {
        let value = cstring(value)?;
        unsafe { self.api.mjs_setString(dest, value.as_ptr()) }
        Ok(())
    }
    /// # Safety
    /// `e` must be a live element belonging to this spec and must not be used again.
    pub unsafe fn delete(&self, e: *mut ffi::mjsElement) -> Result<()> {
        if unsafe { self.api.mjs_delete(self.ptr.as_ptr(), e) } != 0 {
            bail!("Cannot delete model element")
        };
        Ok(())
    }
    pub fn xml(&self) -> Result<String> {
        let mut buf = vec![0i8; 16 * 1024 * 1024];
        let mut err = vec![0i8; 4096];
        let rc = unsafe {
            self.api.mj_saveXMLString(
                self.ptr.as_ptr(),
                buf.as_mut_ptr(),
                buf.len() as i32,
                err.as_mut_ptr(),
                4096,
            )
        };
        if rc != 0 {
            bail!("Cannot serialize model: {}", unsafe { text(err.as_ptr()) })
        };
        Ok(unsafe { text(buf.as_ptr()) })
    }
}
impl Drop for Spec {
    fn drop(&mut self) {
        unsafe { self.api.mj_deleteSpec(self.ptr.as_ptr()) }
    }
}
pub struct Model {
    ptr: NonNull<ffi::mjModel>,
    api: Arc<ffi::MjLib>,
}
unsafe impl Send for Model {}
impl Model {
    fn from_raw(ptr: *mut ffi::mjModel, api: Arc<ffi::MjLib>) -> Result<Self> {
        Ok(Self {
            ptr: NonNull::new(ptr)
                .ok_or_else(|| anyhow!("MuJoCo model allocation/compilation failed"))?,
            api,
        })
    }
    pub fn load(path: &Path) -> Result<Self> {
        Spec::load(path)?.compile()
    }
    pub fn from_xml(xml: &str) -> Result<Self> {
        Spec::from_xml(xml)?.compile()
    }
    pub fn raw(&self) -> &ffi::mjModel {
        unsafe { self.ptr.as_ref() }
    }
    pub fn raw_mut(&mut self) -> &mut ffi::mjModel {
        unsafe { self.ptr.as_mut() }
    }
    pub fn ptr(&self) -> *mut ffi::mjModel {
        self.ptr.as_ptr()
    }
    pub fn api(&self) -> &ffi::MjLib {
        &self.api
    }
    pub fn data(&self) -> Result<Data> {
        Data::new(self)
    }
    pub fn try_clone(&self) -> Result<Model> {
        Self::from_raw(
            unsafe { self.api.mj_copyModel(std::ptr::null_mut(), self.ptr()) },
            self.api.clone(),
        )
    }
    pub fn id(&self, kind: i32, name: &str) -> Result<usize> {
        let n = cstring(name)?;
        let id = unsafe { self.api.mj_name2id(self.ptr(), kind, n.as_ptr()) };
        if id < 0 {
            bail!("Model has no {name:?} (type {kind})")
        };
        Ok(id as usize)
    }
    pub fn name(&self, kind: i32, id: usize) -> String {
        unsafe { text(self.api.mj_id2name(self.ptr(), kind, id as i32)) }
    }
    pub fn total_mass(&self) -> f64 {
        unsafe { self.api.mj_getTotalmass(self.ptr()) }
    }
}
impl Drop for Model {
    fn drop(&mut self) {
        unsafe { self.api.mj_deleteModel(self.ptr()) }
    }
}
pub struct Data {
    ptr: NonNull<ffi::mjData>,
    api: Arc<ffi::MjLib>,
    model: usize,
    nq: usize,
    nv: usize,
    nu: usize,
}
unsafe impl Send for Data {}
impl Data {
    fn new(model: &Model) -> Result<Self> {
        Ok(Self {
            ptr: NonNull::new(unsafe { model.api.mj_makeData(model.ptr()) })
                .ok_or_else(|| anyhow!("MuJoCo data allocation failed"))?,
            api: model.api.clone(),
            model: model.ptr() as usize,
            nq: model.raw().nq as usize,
            nv: model.raw().nv as usize,
            nu: model.raw().nu as usize,
        })
    }
    pub fn raw(&self) -> &ffi::mjData {
        unsafe { self.ptr.as_ref() }
    }
    pub fn raw_mut(&mut self) -> &mut ffi::mjData {
        unsafe { self.ptr.as_mut() }
    }
    pub fn ptr(&self) -> *mut ffi::mjData {
        self.ptr.as_ptr()
    }
    fn check(&self, model: &Model) {
        assert_eq!(
            self.model,
            model.ptr() as usize,
            "MuJoCo data belongs to a different model"
        );
    }
    pub fn step(&mut self, model: &Model) {
        self.check(model);
        unsafe { self.api.mj_step(model.ptr(), self.ptr()) }
    }
    pub fn forward(&mut self, model: &Model) {
        self.check(model);
        unsafe { self.api.mj_forward(model.ptr(), self.ptr()) }
    }
    pub fn reset(&mut self, model: &Model) {
        self.check(model);
        unsafe { self.api.mj_resetData(model.ptr(), self.ptr()) }
    }
    pub fn reset_key(&mut self, model: &Model, key: usize) {
        self.check(model);
        assert!(key < model.raw().nkey as usize);
        unsafe {
            self.api
                .mj_resetDataKeyframe(model.ptr(), self.ptr(), key as i32)
        }
    }
    pub fn qpos(&self) -> &[f64] {
        unsafe { slice(self.raw().qpos, self.nq) }
    }
    pub fn qpos_mut(&mut self) -> &mut [f64] {
        unsafe { slice_mut(self.raw().qpos, self.nq) }
    }
    pub fn qvel(&self) -> &[f64] {
        unsafe { slice(self.raw().qvel, self.nv) }
    }
    pub fn qvel_mut(&mut self) -> &mut [f64] {
        unsafe { slice_mut(self.raw().qvel, self.nv) }
    }
    pub fn ctrl(&self) -> &[f64] {
        unsafe { slice(self.raw().ctrl, self.nu) }
    }
    pub fn ctrl_mut(&mut self) -> &mut [f64] {
        unsafe { slice_mut(self.raw().ctrl, self.nu) }
    }
}
impl Drop for Data {
    fn drop(&mut self) {
        unsafe { self.api.mj_deleteData(self.ptr()) }
    }
}
