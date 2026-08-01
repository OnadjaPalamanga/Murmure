//! Frappe le texte a l'endroit du curseur, dans l'application au premier plan.
//!
//! `SendInput` avec `KEYEVENTF_UNICODE` synthetise des frappes caractere par
//! caractere. C'est ce qu'emploient la plupart des outils de dictee : ca marche
//! dans Win32, UWP, Electron, les navigateurs et les terminaux, sans toucher au
//! presse-papier de l'utilisateur.
//!
//! Ce que Windows fait pour sa propre dictee, lui, c'est du TSF — du texte de
//! composition provisoire, revisable sur place. C'est la bonne facon, mais elle
//! exige un service de texte COM charge dans le processus cible. Hors de
//! proportion ici : on ne frappe que du texte deja valide, jamais provisoire.
//!
//! Deux limites qui ne se contournent pas depuis ici : une application lancee en
//! administrateur refuse les frappes venues d'un processus non eleve (protection
//! Windows), et quelques jeux en raw input les ignorent.

#[cfg(windows)]
pub fn type_text(text: &str) -> Result<bool, String> {
    use windows::Win32::System::Threading::GetCurrentProcessId;
    use windows::Win32::UI::Input::KeyboardAndMouse::{
        SendInput, INPUT, INPUT_0, INPUT_KEYBOARD, KEYBDINPUT, KEYBD_EVENT_FLAGS, KEYEVENTF_KEYUP,
        KEYEVENTF_UNICODE, VIRTUAL_KEY, VK_RETURN,
    };
    use windows::Win32::UI::WindowsAndMessaging::{GetForegroundWindow, GetWindowThreadProcessId};

    // Certaines applications perdent des evenements sur un tableau tres long.
    const BATCH: usize = 256;

    if text.is_empty() {
        return Ok(false);
    }

    // Si c'est une fenetre de Murmure qui a le focus, le texte se taperait dans
    // l'overlay au lieu du document. On compare les processus plutot que les
    // HWND : Tauri expose les siens via une autre version du crate `windows`.
    let mut owner = 0u32;
    unsafe {
        let foreground = GetForegroundWindow();
        if foreground.0.is_null() {
            return Ok(false);
        }
        GetWindowThreadProcessId(foreground, Some(&mut owner));
        if owner == GetCurrentProcessId() {
            return Ok(false);
        }
    }

    fn event(scan: u16, key: VIRTUAL_KEY, flags: KEYBD_EVENT_FLAGS, up: bool) -> INPUT {
        INPUT {
            r#type: INPUT_KEYBOARD,
            Anonymous: INPUT_0 {
                ki: KEYBDINPUT {
                    wVk: key,
                    wScan: scan,
                    dwFlags: if up { flags | KEYEVENTF_KEYUP } else { flags },
                    time: 0,
                    dwExtraInfo: 0,
                },
            },
        }
    }

    let mut inputs: Vec<INPUT> = Vec::with_capacity(text.len() * 2);
    for unit in text.encode_utf16() {
        if unit == b'\r' as u16 {
            continue; // un CRLF ne doit produire qu'un seul saut de ligne
        }
        if unit == b'\n' as u16 {
            // KEYEVENTF_UNICODE ne produit pas de saut de ligne : il faut la
            // vraie touche Entree.
            inputs.push(event(0, VK_RETURN, KEYBD_EVENT_FLAGS(0), false));
            inputs.push(event(0, VK_RETURN, KEYBD_EVENT_FLAGS(0), true));
        } else {
            // Les paires de substitution passent telles quelles : Windows
            // recompose le point de code a partir de deux unites consecutives.
            inputs.push(event(unit, VIRTUAL_KEY(0), KEYEVENTF_UNICODE, false));
            inputs.push(event(unit, VIRTUAL_KEY(0), KEYEVENTF_UNICODE, true));
        }
    }

    for batch in inputs.chunks(BATCH) {
        let sent = unsafe { SendInput(batch, std::mem::size_of::<INPUT>() as i32) };
        if sent as usize != batch.len() {
            return Err(
                "Windows a bloque la frappe. L'application au premier plan est \
                 probablement lancee en administrateur."
                    .into(),
            );
        }
    }

    Ok(true)
}

#[cfg(not(windows))]
pub fn type_text(_text: &str) -> Result<bool, String> {
    Err("La frappe au curseur n'est disponible que sous Windows.".into())
}
