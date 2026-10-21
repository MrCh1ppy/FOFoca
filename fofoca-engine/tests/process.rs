#[test]
fn offline_python_process_contract() {
    let status = std::process::Command::new("python3.12")
        .arg(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/tests/process_contract.py"
        ))
        .arg(env!("CARGO_BIN_EXE_fofoca-engine"))
        .status()
        .expect("Python 3.12 standard library is required for process contract tests");
    assert!(status.success(), "process contract suite failed");
}
