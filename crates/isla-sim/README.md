# isla-sim

Native MuJoCo 3.15.0 integration for Isla Studio. `Model`, `Data` and `Spec` own
their native allocations and keep the dynamic library alive. Model/data handles
move between threads but are not shared for concurrent mutation. The API table
is immutable; Studio must not mutate MuJoCo's global callback pointers.

`src/ffi.rs` is generated from the MuJoCo 3.15.0 public C headers with bindgen's
dynamic-library mode (`MjLib`). The dependency's ABI version is checked before
using any model or data struct. Regenerating bindings requires the headers from
that exact release, bindgen and libclang. Do not update the generated structs
without updating the runtime version check and validating native model tests.

See [native development](../../docs/native-development.md) and
[migration status](../../docs/rust-migration.md). MuJoCo is distributed separately
under Apache-2.0; its upstream copyright and [license](LICENSE-MUJOCO) apply to
its API declarations. The generated declarations have been translated to Rust
and formatted for this workspace; Studio's integration code is MIT licensed.
