use snafu::Snafu;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncWrite, AsyncWriteExt};

use crate::{
    protocol::{self, Command, Response},
    session::Session,
};

/// Fatal transport/serialization failures, not protocol-level request errors.
#[derive(Debug, Snafu)]
pub enum CliError {
    #[snafu(display("Transport I/O failed: {source}"))]
    Io { source: std::io::Error },
    #[snafu(display("Response serialization failed: {source}"))]
    Serialize { source: serde_json::Error },
}

/// Process requests sequentially; flush each response before reading another line.
pub async fn run<R, W>(mut reader: R, mut writer: W) -> Result<(), CliError>
where
    R: AsyncBufRead + Unpin,
    W: AsyncWrite + Unpin,
{
    let mut session = Session;
    let mut line = String::new();
    loop {
        line.clear();
        if reader
            .read_line(&mut line)
            .await
            .map_err(|source| CliError::Io { source })?
            == 0
        {
            return Ok(());
        }
        if line.ends_with('\n') {
            line.pop();
            if line.ends_with('\r') {
                line.pop();
            }
        }
        let (response, quit) = match protocol::parse(&line) {
            Ok((raw, command)) => {
                let quit = matches!(command, Command::Quit {});
                let result = match command {
                    Command::Quit {} => Ok(()),
                    Command::Init { .. } => session.initialize(),
                    Command::Account {}
                    | Command::SetOrders { .. }
                    | Command::AdvanceDay { .. }
                    | Command::Report {} => session.ensure_initialized(),
                };
                let response = match result {
                    Ok(()) => Response::success(&raw),
                    Err(error) => Response::session_failure(&raw, &line, error),
                };
                (response, quit)
            }
            Err(response) => (*response, false),
        };
        let mut bytes =
            serde_json::to_vec(&response).map_err(|source| CliError::Serialize { source })?;
        bytes.push(b'\n');
        writer
            .write_all(&bytes)
            .await
            .map_err(|source| CliError::Io { source })?;
        writer
            .flush()
            .await
            .map_err(|source| CliError::Io { source })?;
        if quit {
            return Ok(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::{
        pin::Pin,
        task::{Context, Poll},
    };

    struct FailingWriter {
        fail_on_flush: bool,
    }

    impl AsyncWrite for FailingWriter {
        fn poll_write(
            self: Pin<&mut Self>,
            _: &mut Context<'_>,
            bytes: &[u8],
        ) -> Poll<std::io::Result<usize>> {
            Poll::Ready(if self.fail_on_flush {
                Ok(bytes.len())
            } else {
                Err(std::io::Error::other("write failed"))
            })
        }
        fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
            Poll::Ready(Err(std::io::Error::other("flush failed")))
        }
        fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
            Poll::Ready(Ok(()))
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn write_and_flush_failures_are_fatal() {
        for fail_on_flush in [false, true] {
            assert!(matches!(
                run(&b"bad\n"[..], FailingWriter { fail_on_flush }).await,
                Err(CliError::Io { .. })
            ));
        }
    }

    #[tokio::test(flavor = "current_thread")]
    async fn buffered_response_is_flushed_before_returning() {
        let mut output = Vec::new();
        run(&b"bad\n"[..], tokio::io::BufWriter::new(&mut output))
            .await
            .unwrap();
        assert!(output.ends_with(b"\n"));
        assert_eq!(
            serde_json::from_slice::<serde_json::Value>(&output).unwrap()["error"]["code"],
            "invalid_json"
        );
    }

    #[tokio::test(flavor = "current_thread")]
    async fn empty_eof_has_no_response() {
        let mut output = Vec::new();
        run(&b""[..], &mut output).await.unwrap();
        assert!(output.is_empty());
    }

    #[tokio::test(flavor = "current_thread")]
    async fn invalid_utf8_is_fatal() {
        let result = run(&b"\xff\n"[..], Vec::new()).await;
        assert!(matches!(result, Err(CliError::Io { .. })));
    }

    #[tokio::test(flavor = "current_thread")]
    async fn quit_ignores_remaining_lines() {
        let mut output = Vec::new();
        run(
            &b"{\"request_id\":\"q\",\"command\":\"quit\",\"params\":{}}\nnot json\n"[..],
            &mut output,
        )
        .await
        .unwrap();
        assert_eq!(output.iter().filter(|&&b| b == b'\n').count(), 1);
    }
}
