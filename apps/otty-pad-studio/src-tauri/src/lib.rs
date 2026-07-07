use serde::{Deserialize, Serialize};
use std::path::Path;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::Duration;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
struct Pane {
    id: String,
    window_id: String,
    tab_id: String,
    index: i64,
    active: bool,
    cwd: String,
    process: String,
    cols: i64,
    rows: i64,
    #[serde(default)]
    agent: bool,
}

#[derive(Debug, Deserialize)]
struct OttyPayload<T> {
    data: T,
}

fn looks_like_agent(title: &str) -> bool {
    let trimmed = title.trim();
    if trimmed.is_empty() {
        return false;
    }
    let lower = trimmed.to_lowercase();
    if ["vim ", "nvim ", "vi ", "emacs ", "nano ", "code "]
        .iter()
        .any(|prefix| lower.starts_with(prefix))
    {
        return false;
    }
    let first = trimmed.chars().next().unwrap_or_default();
    ((0x2800..=0x28ff).contains(&(first as u32)))
        || matches!(first, '✳' | '⏺' | '●')
        || ["claude", "codex", "opencode"].iter().any(|word| lower.contains(word))
}

fn run_otty(args: &[&str]) -> Result<String, String> {
    let binary = resolve_otty_bin()?;
    let mut command = Command::new(binary);
    command.args(args).stdout(Stdio::piped()).stderr(Stdio::piped());
    let output = run_with_timeout(command, Duration::from_secs(6))?;
    if !output.status.success() {
        let message = String::from_utf8_lossy(&output.stderr).trim().to_string();
        return Err(if message.is_empty() {
            format!("otty {} failed", args.join(" "))
        } else {
            message
        });
    }
    Ok(String::from_utf8_lossy(&output.stdout).to_string())
}

fn resolve_otty_bin() -> Result<String, String> {
    let mut candidates: Vec<String> = [
        std::env::var("OTTY_BIN").ok(),
        std::env::var("OTTY_BIN_DIR").ok().map(|dir| format!("{dir}/otty")),
        Some("/usr/local/bin/otty".to_string()),
        Some("/opt/homebrew/bin/otty".to_string()),
        Some("/Applications/Otty.app/Contents/MacOS/otty-cli".to_string()),
    ]
    .into_iter()
    .flatten()
    .collect();
    candidates.extend(path_candidates("otty"));
    candidates
        .into_iter()
        .find(|path| !path.trim().is_empty() && Path::new(path).is_file())
        .ok_or_else(|| "Otty CLI unavailable: no trusted otty binary found".to_string())
}

fn path_candidates(binary: &str) -> Vec<String> {
    std::env::var_os("PATH")
        .map(|paths| {
            std::env::split_paths(&paths)
                .map(|dir| dir.join(binary).to_string_lossy().to_string())
                .collect()
        })
        .unwrap_or_default()
}

fn run_with_timeout(mut command: Command, timeout: Duration) -> Result<std::process::Output, String> {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let result = command.output().map_err(|err| format!("Otty CLI failed to start: {err}"));
        let _ = tx.send(result);
    });
    rx.recv_timeout(timeout)
        .map_err(|_| "Otty CLI timed out".to_string())?
}

fn send_args<'a>(pane_id: &'a str, prompt: &'a str, submit: bool) -> Result<Vec<&'a str>, String> {
    if pane_id.trim().is_empty() {
        return Err("pane id is required; refusing to send to the focused pane".into());
    }
    if prompt.trim().is_empty() {
        return Err("prompt is empty; nothing was sent".into());
    }
    let mut args = vec!["pane", "send-keys", "--pane", pane_id, "--bracketed-paste", "--", prompt];
    if submit {
        args.push("key:Enter");
    }
    Ok(args)
}

#[tauri::command]
fn pane_list() -> Result<Vec<Pane>, String> {
    let stdout = run_otty(&["--format", "json", "pane", "list"])?;
    let payload: OttyPayload<Vec<Pane>> = serde_json::from_str(&stdout)
        .map_err(|err| format!("Could not parse Otty pane list JSON: {err}"))?;
    Ok(payload
        .data
        .into_iter()
        .map(|mut pane| {
            pane.agent = looks_like_agent(&pane.process);
            pane
        })
        .collect())
}

#[tauri::command]
fn send_prompt(pane_id: String, prompt: String, submit: bool) -> Result<(), String> {
    let pane_id = pane_id.trim();
    let args = send_args(pane_id, &prompt, submit)?;
    run_otty(&args).map(|_| ())
}

#[tauri::command]
fn capture_pane(pane_id: String) -> Result<String, String> {
    let pane_id = pane_id.trim();
    if pane_id.is_empty() {
        return Err("pane id is required before capture".into());
    }
    run_otty(&["pane", "capture", pane_id, "--trim"])
}

#[tauri::command]
fn otty_info() -> Result<serde_json::Value, String> {
    let version = match run_otty(&["version"]) {
        Ok(version) => version,
        Err(error) => {
            return Ok(serde_json::json!({
                "available": false,
                "version": "unknown",
                "sendKeysEnabled": false,
                "error": error
            }));
        }
    };
    let send_keys = run_otty(&["config", "get", "ipc-allow-send-keys"])
        .map(|value| value.to_lowercase().contains("true"))
        .unwrap_or(false);
    Ok(serde_json::json!({
        "available": true,
        "version": version.trim(),
        "sendKeysEnabled": send_keys
    }))
}

pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![pane_list, send_prompt, capture_pane, otty_info])
        .run(tauri::generate_context!())
        .expect("error while running Otty Pad Studio");
}

#[cfg(test)]
mod tests {
    use super::{looks_like_agent, path_candidates, send_args};

    #[test]
    fn agent_detection_ignores_editor_buffers() {
        assert!(looks_like_agent("⠐ Review failing CI"));
        assert!(looks_like_agent("OpenCode session"));
        assert!(!looks_like_agent("vim claude-notes.md"));
        assert!(!looks_like_agent(""));
    }

    #[test]
    fn send_args_refuse_empty_target_or_prompt() {
        assert!(send_args("", "hello", true).is_err());
        assert!(send_args("p_1", " ", true).is_err());
    }

    #[test]
    fn send_args_match_safe_otty_shape() {
        assert_eq!(
            send_args("p_1", "hello", true).unwrap(),
            vec!["pane", "send-keys", "--pane", "p_1", "--bracketed-paste", "--", "hello", "key:Enter"]
        );
        assert_eq!(
            send_args("p_1", "hello", false).unwrap(),
            vec!["pane", "send-keys", "--pane", "p_1", "--bracketed-paste", "--", "hello"]
        );
    }

    #[test]
    fn send_args_preserves_prompt_bytes() {
        assert_eq!(send_args("p_1", "  hello\n", false).unwrap()[6], "  hello\n");
    }

    #[test]
    fn path_candidates_include_binary_name() {
        let candidates = path_candidates("otty-test-bin");
        assert!(candidates.iter().all(|candidate| candidate.ends_with("otty-test-bin")));
    }
}
