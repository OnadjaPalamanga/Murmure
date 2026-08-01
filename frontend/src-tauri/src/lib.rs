use std::path::PathBuf;
use std::process::Child;
use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

/// Attention : Ctrl+Space est aussi l'autocompletion de la plupart des IDE.
/// Un raccourci global le confisque a l'echelle du systeme.
const DEFAULT_HOTKEY: &str = "Ctrl+Space";

/// Hauteurs de l'overlay selon la phase. L'ecoute est une pastille compacte ;
/// la relecture doit laisser respirer plusieurs lignes de texte.
const H_LISTENING: f64 = 132.0;
const H_REVIEW: f64 = 340.0;
const OVERLAY_WIDTH: f64 = 460.0;
/// Marge au-dessus de la barre des taches.
const BOTTOM_MARGIN: f64 = 96.0;

#[derive(Default)]
struct Hotkey {
    current: Option<Shortcut>,
    /// Dernier echec d'enregistrement, pour que l'UI puisse le signaler au lieu
    /// de laisser croire que le raccourci fonctionne.
    error: Option<String>,
}

struct HotkeyState(Mutex<Hotkey>);

/// Le service Python lance par l'application, pour qu'il n'y ait qu'une seule
/// chose a demarrer. Vide si un service tournait deja : on ne le double pas.
struct ServiceProcess(Mutex<Option<Child>>);

/// Cherche `backend/.venv/Scripts/pythonw.exe` en remontant depuis l'executable.
/// Fonctionne aussi bien depuis target/debug que depuis un dossier installe.
fn find_service_python() -> Option<(PathBuf, PathBuf)> {
    if let Ok(root) = std::env::var("MURMURE_ROOT") {
        let root = PathBuf::from(root);
        let python = root.join("backend/.venv/Scripts/pythonw.exe");
        if python.exists() {
            return Some((python, root.join("backend")));
        }
    }

    let exe = std::env::current_exe().ok()?;
    let mut dir = exe.parent()?;
    for _ in 0..6 {
        let python = dir.join("backend/.venv/Scripts/pythonw.exe");
        if python.exists() {
            return Some((python, dir.join("backend")));
        }
        dir = dir.parent()?;
    }
    None
}

fn service_is_running() -> bool {
    // Un simple TCP connect suffit : pas besoin de client HTTP pour ca.
    std::net::TcpStream::connect_timeout(
        &"127.0.0.1:8756".parse().unwrap(),
        std::time::Duration::from_millis(400),
    )
    .is_ok()
}

fn spawn_service() -> Option<Child> {
    if service_is_running() {
        return None;
    }

    let (python, cwd) = find_service_python()?;
    let mut command = std::process::Command::new(python);
    command.arg("-m").arg("murmure").current_dir(cwd);

    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    match command.spawn() {
        Ok(child) => Some(child),
        Err(err) => {
            eprintln!("Service Python non demarre : {err}");
            None
        }
    }
}

fn overlay(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("overlay")
}

/// Place l'overlay en bas au centre de l'ecran ou se trouve le curseur, pour
/// qu'il apparaisse sur le bon moniteur dans une configuration multi-ecrans.
fn position_overlay(win: &WebviewWindow, height: f64) {
    let monitor = win
        .cursor_position()
        .ok()
        .and_then(|p| win.monitor_from_point(p.x, p.y).ok().flatten())
        .or_else(|| win.current_monitor().ok().flatten());

    let Some(monitor) = monitor else { return };

    let scale = monitor.scale_factor();
    let size = monitor.size().to_logical::<f64>(scale);
    let pos = monitor.position().to_logical::<f64>(scale);

    let _ = win.set_size(LogicalSize::new(OVERLAY_WIDTH, height));
    let _ = win.set_position(LogicalPosition::new(
        pos.x + (size.width - OVERLAY_WIDTH) / 2.0,
        pos.y + size.height - height - BOTTOM_MARGIN,
    ));
}

#[tauri::command]
fn show_overlay(app: AppHandle, review: bool) {
    if let Some(win) = overlay(&app) {
        position_overlay(&win, if review { H_REVIEW } else { H_LISTENING });
        let _ = win.show();
        // Pendant l'ecoute on ne vole pas le focus : l'application dans laquelle
        // l'utilisateur travaille doit garder son curseur. En relecture au
        // contraire, il faut pouvoir editer et copier tout de suite.
        if review {
            let _ = win.set_focus();
        }
    }
}

#[tauri::command]
fn hide_overlay(app: AppHandle) {
    if let Some(win) = overlay(&app) {
        let _ = win.hide();
    }
}

#[tauri::command]
fn resize_overlay(app: AppHandle, height: f64) {
    if let Some(win) = overlay(&app) {
        position_overlay(&win, height.clamp(H_LISTENING, 720.0));
    }
}

/// Declenche une dictee sans passer par le raccourci global : utilise par le
/// bouton de la fenetre principale et par l'entree « Dicter » du menu systeme.
#[tauri::command]
fn trigger_dictation(app: AppHandle) {
    if let Some(win) = overlay(&app) {
        position_overlay(&win, H_LISTENING);
        let _ = win.show();
        // "toggle" : premier appel demarre, second termine — quel que soit le
        // mode configure, puisqu'il n'y a pas de touche a relacher ici.
        let _ = win.emit("hotkey", "toggle");
    }
}

#[tauri::command]
fn open_main(app: AppHandle, tab: Option<String>) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
        if let Some(tab) = tab {
            let _ = win.emit("navigate", tab);
        }
    }
}

/// (Re)enregistre le raccourci global. Le precedent est libere d'abord.
#[tauri::command]
fn set_hotkey(app: AppHandle, accelerator: String) -> Result<(), String> {
    let state = app.state::<HotkeyState>();
    let mut hotkey = state.0.lock().map_err(|e| e.to_string())?;

    let result = (|| -> Result<Shortcut, String> {
        let shortcut: Shortcut = accelerator.parse().map_err(|_| {
            format!("Raccourci invalide : « {accelerator} ». Format attendu : Ctrl+Alt+D")
        })?;

        if let Some(previous) = hotkey.current.take() {
            let _ = app.global_shortcut().unregister(previous);
        }
        app.global_shortcut().register(shortcut).map_err(|e| {
            format!("« {accelerator} » est déjà utilisé par une autre application ({e}). Choisis-en un autre.")
        })?;
        Ok(shortcut)
    })();

    match result {
        Ok(shortcut) => {
            hotkey.current = Some(shortcut);
            hotkey.error = None;
            Ok(())
        }
        Err(message) => {
            hotkey.error = Some(message.clone());
            Err(message)
        }
    }
}

/// L'UI interroge cet etat au chargement : la fenetre principale n'existe pas
/// encore quand le raccourci par defaut est enregistre, un evenement serait perdu.
#[tauri::command]
fn hotkey_status(app: AppHandle) -> Result<Option<String>, String> {
    let state = app.state::<HotkeyState>();
    let hotkey = state.0.lock().map_err(|e| e.to_string())?;
    Ok(hotkey.error.clone())
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    let dictate = MenuItem::with_id(app, "dictate", "Dicter", true, None::<&str>)?;
    let history = MenuItem::with_id(app, "history", "Historique", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Reglages", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quitter", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&dictate, &history, &settings, &quit])?;

    TrayIconBuilder::with_id("murmure")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Murmure — dictee locale")
        .menu(&menu)
        .show_menu_on_left_click(false)
        .on_tray_icon_event(|tray, event| match event {
            TrayIconEvent::Click {
                button: MouseButton::Left,
                button_state: MouseButtonState::Up,
                ..
            }
            | TrayIconEvent::DoubleClick {
                button: MouseButton::Left,
                ..
            } => open_main(tray.app_handle().clone(), None),
            _ => {}
        })
        .on_menu_event(|app, event| match event.id.as_ref() {
            "dictate" => trigger_dictation(app.clone()),
            "history" => open_main(app.clone(), Some("history".into())),
            "settings" => open_main(app.clone(), Some("settings".into())),
            "quit" => {
                // On n'arrete que le service qu'on a nous-meme lance.
                if let Some(mut child) = app.state::<ServiceProcess>().0.lock().unwrap().take() {
                    let _ = child.kill();
                }
                app.exit(0)
            }
            _ => {}
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .plugin(
            tauri_plugin_global_shortcut::Builder::new()
                .with_handler(|app, _shortcut, event| {
                    // Le mode (maintien ou bascule) est decide cote JS, qui
                    // connait les reglages ; ici on ne fait que transmettre.
                    let phase = match event.state() {
                        ShortcutState::Pressed => "pressed",
                        ShortcutState::Released => "released",
                    };
                    if let Some(win) = overlay(app) {
                        let _ = win.emit("hotkey", phase);
                    }
                })
                .build(),
        )
        .manage(HotkeyState(Mutex::new(Hotkey::default())))
        .manage(ServiceProcess(Mutex::new(spawn_service())))
        .invoke_handler(tauri::generate_handler![
            show_overlay,
            hide_overlay,
            resize_overlay,
            open_main,
            trigger_dictation,
            set_hotkey,
            hotkey_status
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;

            // L'echec est conserve dans HotkeyState et affiche par l'UI au
            // chargement : un raccourci mort en silence est le pire des cas.
            if let Err(err) = set_hotkey(handle.clone(), DEFAULT_HOTKEY.into()) {
                eprintln!("Raccourci par defaut non enregistre : {err}");
            }

            // Fermer la fenetre principale renvoie dans la zone de notification
            // au lieu de tuer le service : le raccourci doit rester vivant.
            if let Some(main) = handle.get_webview_window("main") {
                let main_clone = main.clone();
                main.on_window_event(move |event| {
                    if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                        api.prevent_close();
                        let _ = main_clone.hide();
                    }
                });
            }
            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("erreur au lancement de Murmure");
}
