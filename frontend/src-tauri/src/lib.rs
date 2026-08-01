use std::sync::Mutex;

use tauri::{
    menu::{Menu, MenuItem},
    tray::TrayIconBuilder,
    AppHandle, Emitter, LogicalPosition, LogicalSize, Manager, WebviewWindow,
};
use tauri_plugin_global_shortcut::{GlobalShortcutExt, Shortcut, ShortcutState};

const DEFAULT_HOTKEY: &str = "Ctrl+Alt+Space";

/// Hauteurs de l'overlay selon la phase. L'ecoute est une pastille compacte ;
/// la relecture doit laisser respirer plusieurs lignes de texte.
const H_LISTENING: f64 = 132.0;
const H_REVIEW: f64 = 340.0;
const OVERLAY_WIDTH: f64 = 460.0;
/// Marge au-dessus de la barre des taches.
const BOTTOM_MARGIN: f64 = 96.0;

struct HotkeyState(Mutex<Option<Shortcut>>);

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

#[tauri::command]
fn open_main(app: AppHandle, tab: Option<String>) {
    if let Some(win) = app.get_webview_window("main") {
        let _ = win.show();
        let _ = win.unminimize();
        let _ = win.set_focus();
        let _ = win.emit("navigate", tab.unwrap_or_else(|| "history".into()));
    }
}

/// (Re)enregistre le raccourci global. Le precedent est libere d'abord.
#[tauri::command]
fn set_hotkey(app: AppHandle, accelerator: String) -> Result<(), String> {
    let shortcut: Shortcut = accelerator.parse().map_err(|_| {
        format!("Raccourci invalide : {accelerator}. Exemple attendu : Ctrl+Alt+Space")
    })?;

    let state = app.state::<HotkeyState>();
    let mut current = state.0.lock().map_err(|e| e.to_string())?;

    if let Some(previous) = current.take() {
        let _ = app.global_shortcut().unregister(previous);
    }
    app.global_shortcut()
        .register(shortcut)
        .map_err(|e| e.to_string())?;
    *current = Some(shortcut);
    Ok(())
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
        .on_menu_event(|app, event| match event.id.as_ref() {
            "dictate" => {
                if let Some(win) = overlay(app) {
                    position_overlay(&win, H_LISTENING);
                    let _ = win.show();
                    let _ = win.emit("hotkey", "toggle");
                }
            }
            "history" => open_main(app.clone(), Some("history".into())),
            "settings" => open_main(app.clone(), Some("settings".into())),
            "quit" => app.exit(0),
            _ => {}
        })
        .build(app)?;
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
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
        .manage(HotkeyState(Mutex::new(None)))
        .invoke_handler(tauri::generate_handler![
            show_overlay,
            hide_overlay,
            resize_overlay,
            open_main,
            set_hotkey
        ])
        .setup(|app| {
            let handle = app.handle().clone();
            build_tray(&handle)?;

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
