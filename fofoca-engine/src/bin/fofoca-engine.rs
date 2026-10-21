use std::process::ExitCode;

fn main() -> ExitCode {
    let runtime = match tokio::runtime::Builder::new_current_thread().build() {
        Ok(runtime) => runtime,
        Err(_) => {
            eprintln!("fofoca-engine: runtime initialization failed");
            return ExitCode::FAILURE;
        }
    };
    match runtime.block_on(fofoca_engine::run(
        tokio::io::BufReader::new(tokio::io::stdin()),
        tokio::io::stdout(),
    )) {
        Ok(()) => ExitCode::SUCCESS,
        Err(_) => {
            // Do not leak request contents or uncontrolled OS diagnostics.
            eprintln!("fofoca-engine: protocol serialization or transport I/O failed");
            ExitCode::FAILURE
        }
    }
}
