use anyhow::{bail, Context, Result};
use serde_json::Value;
use std::{
    collections::BTreeSet,
    fs,
    io::Write,
    path::{Component, Path, PathBuf},
};

#[derive(Clone, Debug)]
pub struct Paths {
    pub root: PathBuf,
    pub robots: PathBuf,
    pub runs: PathBuf,
    pub policies: PathBuf,
    pub settings: PathBuf,
    pub cache: PathBuf,
    pub ui: PathBuf,
}

impl Paths {
    pub fn discover() -> Result<Self> {
        let root = if let Some(path) = std::env::var_os("ISLA_ROOT") {
            PathBuf::from(path)
        } else {
            let current = std::env::current_dir()?;
            let executable = std::env::current_exe()?;
            let beside = executable.parent().unwrap_or(&current);
            [current.clone(), beside.join("studio"), beside.to_owned(), PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../..")]
                .into_iter().find(|p| p.join("robots").is_dir() && p.join("ui").is_dir())
                .context("Cannot find Studio's assets. Set ISLA_ROOT to the folder containing robots/, examples/ and ui/.")?
        };
        Self::from_root(root)
    }

    pub fn from_root(root: impl Into<PathBuf>) -> Result<Self> {
        let root = fs::canonicalize(root.into())?;
        let location = |key: &str, name: &str| {
            std::env::var_os(key)
                .map(PathBuf::from)
                .unwrap_or_else(|| root.join(name))
        };
        Ok(Self {
            robots: location("ISLA_ROBOTS", "robots"),
            runs: location("ISLA_RUNS", "runs"),
            policies: location("ISLA_POLICIES", "policies"),
            settings: location("ISLA_SETTINGS", "settings"),
            cache: location("ISLA_CACHE", ".cache"),
            ui: root.join("ui/dist"),
            root,
        })
    }

    pub fn ensure(&self) -> Result<()> {
        for path in [
            &self.robots,
            &self.runs,
            &self.policies,
            &self.settings,
            &self.cache,
        ] {
            fs::create_dir_all(path)?;
        }
        offer(&self.root.join("robots"), &self.robots, ".bundled")?;
        offer(&self.root.join("examples/runs"), &self.runs, ".examples")?;
        Ok(())
    }

    pub fn run(&self, name: &str) -> Result<PathBuf> {
        validate_name(name)?;
        let path = confined(&self.runs, name)?;
        if !path.join("setup.json").is_file() {
            bail!("No run named {name}");
        }
        Ok(path)
    }
}

pub fn validate_name(name: &str) -> Result<()> {
    if name.is_empty()
        || name.len() > 120
        || name.starts_with('.')
        || name.ends_with(['.', ' '])
        || !name
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | ' ' | '.'))
        || name.contains("..")
    {
        bail!("Use a name of 1 to 120 letters, numbers, spaces, dots, underscores or dashes");
    }
    let stem = name.split('.').next().unwrap_or("").to_ascii_uppercase();
    if [
        "CON", "PRN", "AUX", "NUL", "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8",
        "COM9", "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
    ]
    .contains(&stem.as_str())
    {
        bail!("That name is reserved by Windows");
    }
    Ok(())
}

/// Confine both existing and new paths, including symlinks/junctions in parents.
pub fn confined(root: &Path, relative: impl AsRef<Path>) -> Result<PathBuf> {
    let relative = relative.as_ref();
    if relative.as_os_str().is_empty()
        || relative
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
        || relative.to_string_lossy().contains([':', '\\'])
    {
        bail!("Path is outside the project folder");
    }
    let base = fs::canonicalize(root)?;
    let mut target = base.clone();
    for part in relative.components() {
        target.push(part.as_os_str());
        if fs::symlink_metadata(&target).is_ok() {
            let resolved = fs::canonicalize(&target)?;
            if !resolved.starts_with(&base) {
                bail!("Path is outside the project folder");
            }
            target = resolved;
        }
    }
    Ok(target)
}

pub fn read_json(path: &Path) -> Result<Value> {
    serde_json::from_slice(
        &fs::read(path).with_context(|| format!("Cannot read {}", path.display()))?,
    )
    .with_context(|| format!("Invalid JSON in {}", path.display()))
}

pub fn write_json(path: &Path, value: &Value) -> Result<()> {
    atomic_write(path, &serde_json::to_vec_pretty(value)?)
}

pub fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().context("Missing parent folder")?;
    fs::create_dir_all(parent)?;
    let mut temporary = tempfile::NamedTempFile::new_in(parent)?;
    temporary.write_all(bytes)?;
    temporary.write_all(b"\n")?;
    temporary.as_file().sync_all()?;
    temporary.persist(path)?;
    Ok(())
}

pub fn copy_tree(source: &Path, target: &Path) -> Result<()> {
    fs::create_dir_all(target)?;
    for entry in fs::read_dir(source)? {
        let entry = entry?;
        let kind = entry.file_type()?;
        if kind.is_symlink() {
            bail!(
                "Bundled assets may not contain symlinks: {}",
                entry.path().display()
            );
        }
        if kind.is_dir() {
            copy_tree(&entry.path(), &target.join(entry.file_name()))?;
        } else if kind.is_file() {
            fs::copy(entry.path(), target.join(entry.file_name()))?;
        }
    }
    Ok(())
}

pub fn offer(source: &Path, target: &Path, record: &str) -> Result<()> {
    if !source.is_dir() || fs::canonicalize(source)? == fs::canonicalize(target)? {
        return Ok(());
    }
    let mut offered: BTreeSet<String> = fs::read_to_string(target.join(record))
        .unwrap_or_default()
        .split_whitespace()
        .map(str::to_owned)
        .collect();
    let mut entries = fs::read_dir(source)?.collect::<std::io::Result<Vec<_>>>()?;
    entries.sort_by_key(|e| e.file_name());
    for entry in entries {
        if !entry.file_type()?.is_dir() {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if offered.contains(&name) {
            continue;
        }
        validate_name(&name)?;
        let destination = confined(target, &name)?;
        if destination.exists() {
            offered.insert(name);
            continue;
        }
        // Stage a complete copy first. A failed copy must never reserve a partial example.
        let staging = tempfile::tempdir_in(target)?;
        let staged = staging.path().join(&name);
        copy_tree(&entry.path(), &staged)?;
        if destination.exists() {
            offered.insert(name);
            continue;
        }
        fs::rename(staged, destination)?;
        offered.insert(name);
    }
    atomic_write(
        &target.join(record),
        offered
            .into_iter()
            .collect::<Vec<_>>()
            .join("\n")
            .as_bytes(),
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reject_traversal_and_windows_aliases() {
        for name in [
            "",
            "..",
            "../outside",
            "C:\\outside",
            "a/b",
            "a\\b",
            "NUL.txt",
            "trailing.",
            "bad:stream",
        ] {
            assert!(validate_name(name).is_err(), "{name}");
        }
        assert!(validate_name("luma1-walk").is_ok());
        let temp = tempfile::tempdir().unwrap();
        assert!(confined(temp.path(), "../escape").is_err());
        assert!(confined(temp.path(), "/absolute").is_err());
        assert!(confined(temp.path(), "folder/model.xml").is_ok());
    }
    #[test]
    fn examples_never_replace_users_or_return_after_deletion() -> Result<()> {
        let root = tempfile::tempdir()?;
        let source = root.path().join("source");
        let target = root.path().join("target");
        fs::create_dir_all(source.join("sample"))?;
        fs::create_dir_all(&target)?;
        fs::write(source.join("sample/policy.json"), b"original")?;
        offer(&source, &target, ".examples")?;
        fs::write(target.join("sample/policy.json"), b"user")?;
        offer(&source, &target, ".examples")?;
        assert_eq!(fs::read(target.join("sample/policy.json"))?, b"user");
        fs::remove_dir_all(target.join("sample"))?;
        offer(&source, &target, ".examples")?;
        assert!(!target.join("sample").exists());
        Ok(())
    }
}
