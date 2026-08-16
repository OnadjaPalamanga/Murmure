mod inject;

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

/// Un echec d'enregistrement du raccourci, sous une forme que l'interface peut
/// traduire. On rend un code et ses parametres plutot qu'une phrase toute
/// faite : l'interface existe en deux langues, et c'est elle qui sait laquelle
/// est affichee. `detail` porte le message d'origine, utile tel quel.
#[derive(Clone, Debug, serde::Serialize)]
struct HotkeyError {
    code: &'static str,
    accelerator: String,
    detail: String,
}

impl std::fmt::Display for HotkeyError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{} ({}) {}", self.code, self.accelerator, self.detail)
    }
}

#[derive(Default)]
struct Hotkey {
    current: Option<Shortcut>,
    /// Dernier echec d'enregistrement, pour que l'UI puisse le signaler au lieu
    /// de laisser croire que le raccourci fonctionne.
    error: Option<HotkeyError>,
}

struct HotkeyState(Mutex<Hotkey>);

/// Les entrees du menu de la zone de notification, gardees pour pouvoir les
/// renommer quand la langue change. Le menu est construit avant qu'aucune page
/// ne s'affiche : il demarre donc dans la langue par defaut, et l'interface le
/// corrige des qu'elle sait laquelle l'utilisateur a choisie.
struct TrayItems {
    dictate: MenuItem<tauri::Wry>,
    history: MenuItem<tauri::Wry>,
    settings: MenuItem<tauri::Wry>,
    quit: MenuItem<tauri::Wry>,
}

struct TrayMenu(Mutex<Option<TrayItems>>);

#[derive(serde::Deserialize)]
struct TrayLabels {
    dictate: String,
    history: String,
    settings: String,
    quit: String,
    tooltip: String,
}

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

/// Revision des reglages attendue du service. Doit valoir `SETTINGS_REVISION`
/// dans `backend/src/murmure/server.py` : les deux montent ensemble des qu'un
/// reglage est ajoute, retire ou change de sens.
const SETTINGS_REVISION: u32 = 5;

/// Ce que repond le service sur le port 8756, s'il repond.
enum ServiceState {
    /// Personne n'ecoute.
    Absent,
    /// Un service de la bonne revision : on s'y raccroche.
    Current,
    /// Un service, mais d'une autre revision. Le cas courant en developpement :
    /// celui lance par `run.ps1` survit a la fermeture de sa console et garde le
    /// port pendant des jours. L'interface qui s'y raccroche pilote alors un
    /// backend qui ignore la moitie de ses reglages, sans qu'aucune erreur ne
    /// le dise — les menus sont juste vides.
    Stale,
}

/// Interroge `/health` en HTTP brut. Un client HTTP complet serait
/// disproportionne pour une requete vers 127.0.0.1 dont on ne lit qu'un entier.
fn probe_service() -> ServiceState {
    use std::io::{Read, Write};

    let addr = "127.0.0.1:8756".parse().unwrap();
    let timeout = std::time::Duration::from_millis(600);
    let Ok(mut stream) = std::net::TcpStream::connect_timeout(&addr, timeout) else {
        return ServiceState::Absent;
    };
    let _ = stream.set_read_timeout(Some(timeout));
    let _ = stream.set_write_timeout(Some(timeout));

    if stream
        .write_all(b"GET /health HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n")
        .is_err()
    {
        // Quelque chose ecoute sans repondre : ce n'est pas notre service, et
        // le tuer serait presomptueux. On le laisse et on ne demarre rien.
        return ServiceState::Current;
    }

    let mut body = String::new();
    if stream.read_to_string(&mut body).is_err() {
        return ServiceState::Current;
    }

    // Le JSON est produit par nous et tient sur une ligne : chercher la cle
    // suffit, et evite d'embarquer serde pour un seul entier.
    let found = body
        .split("\"settings_revision\"")
        .nth(1)
        .and_then(|rest| rest.split([',', '}']).next())
        .and_then(|value| {
            value
                .trim_start_matches(&[':', ' '][..])
                .trim()
                .parse::<u32>()
                .ok()
        });

    match found {
        Some(revision) if revision == SETTINGS_REVISION => ServiceState::Current,
        _ => ServiceState::Stale,
    }
}

/// Termine le service qui occupe le port sans etre a la bonne revision.
/// On ne devine pas son PID : le service se coupe lui-meme sur `/shutdown`.
#[cfg(windows)]
fn stop_stale_service() {
    use std::io::Write;

    let addr = "127.0.0.1:8756".parse().unwrap();
    let timeout = std::time::Duration::from_millis(600);
    if let Ok(mut stream) = std::net::TcpStream::connect_timeout(&addr, timeout) {
        let _ = stream.set_write_timeout(Some(timeout));
        let _ = stream.write_all(b"POST /shutdown HTTP/1.0\r\nHost: 127.0.0.1\r\n\r\n");
    }
    // Laisser au processus le temps de rendre le port avant de le reprendre.
    for _ in 0..20 {
        std::thread::sleep(std::time::Duration::from_millis(100));
        if std::net::TcpStream::connect_timeout(&addr, std::time::Duration::from_millis(150))
            .is_err()
        {
            return;
        }
    }
    eprintln!("Le service perime n'a pas rendu le port 8756.");
}

#[cfg(not(windows))]
fn stop_stale_service() {}

fn spawn_service() -> Option<Child> {
    match probe_service() {
        ServiceState::Current => return None,
        ServiceState::Stale => {
            eprintln!("Service perime sur le port 8756 : arret avant de relancer.");
            stop_stale_service();
        }
        ServiceState::Absent => {}
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

/// `review` regle la hauteur, `focus` dit si l'on prend la main. Les deux vont
/// ensemble sauf en dictee continue, ou l'overlay doit etre haut pour montrer le
/// texte qui s'accumule tout en laissant le curseur dans le document — c'est
/// justement la qu'on est en train d'ecrire.
#[tauri::command]
fn show_overlay(app: AppHandle, review: bool, focus: Option<bool>) {
    if let Some(win) = overlay(&app) {
        position_overlay(&win, if review { H_REVIEW } else { H_LISTENING });
        let _ = win.show();
        // Pendant l'ecoute on ne vole pas le focus : l'application dans laquelle
        // l'utilisateur travaille doit garder son curseur. En relecture au
        // contraire, il faut pouvoir editer et copier tout de suite.
        if focus.unwrap_or(review) {
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
fn open_audio_folder() -> Result<(), String> {
    let app_data = std::env::var_os("APPDATA")
        .map(PathBuf::from)
        .ok_or_else(|| "Dossier de donnees Windows introuvable".to_string())?;
    let audio_dir = app_data.join("Murmure").join("audio");
    std::fs::create_dir_all(&audio_dir).map_err(|err| err.to_string())?;

    #[cfg(target_os = "windows")]
    std::process::Command::new("explorer.exe")
        .arg(&audio_dir)
        .spawn()
        .map_err(|err| err.to_string())?;

    Ok(())
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

/// Frappe une phrase validee a l'endroit du curseur. Retourne `false` si le
/// focus etait sur Murmure lui-meme : l'appelant retombe alors sur le
/// presse-papier plutot que de taper dans sa propre fenetre.
#[tauri::command]
fn type_text(text: String) -> Result<bool, String> {
    inject::type_text(&text)
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
fn set_hotkey(app: AppHandle, accelerator: String) -> Result<(), HotkeyError> {
    let fail = |code: &'static str, detail: String| HotkeyError {
        code,
        accelerator: accelerator.clone(),
        detail,
    };

    let state = app.state::<HotkeyState>();
    let mut hotkey = state.0.lock().map_err(|e| fail("invalid", e.to_string()))?;

    let result = (|| -> Result<Shortcut, HotkeyError> {
        let shortcut: Shortcut = accelerator
            .parse()
            .map_err(|_| fail("invalid", String::new()))?;

        if let Some(previous) = hotkey.current.take() {
            let _ = app.global_shortcut().unregister(previous);
        }
        app.global_shortcut()
            .register(shortcut)
            .map_err(|e| fail("taken", e.to_string()))?;
        Ok(shortcut)
    })();

    match result {
        Ok(shortcut) => {
            hotkey.current = Some(shortcut);
            hotkey.error = None;
            Ok(())
        }
        Err(error) => {
            hotkey.error = Some(error.clone());
            Err(error)
        }
    }
}

/// L'UI interroge cet etat au chargement : la fenetre principale n'existe pas
/// encore quand le raccourci par defaut est enregistre, un evenement serait perdu.
#[tauri::command]
fn hotkey_status(app: AppHandle) -> Result<Option<HotkeyError>, String> {
    let state = app.state::<HotkeyState>();
    let hotkey = state.0.lock().map_err(|e| e.to_string())?;
    Ok(hotkey.error.clone())
}

/// Renomme le menu de la zone de notification. Appele par l'interface au
/// demarrage et a chaque changement de langue : le menu vit cote Rust, il ne
/// peut pas lire le catalogue de traduction, on lui envoie donc les libelles.
#[tauri::command]
/// Rend `false` si le menu n'existe pas encore : l'appelant reessaiera.
fn set_tray_labels(app: AppHandle, labels: TrayLabels) -> Result<bool, String> {
    // `try_state` et non `state` : la page se charge pendant que `setup` tourne,
    // et elle appelle cette commande des qu'elle sait quelle langue afficher.
    // `state` paniquerait si le menu n'etait pas encore construit.
    let Some(state) = app.try_state::<TrayMenu>() else {
        return Ok(false);
    };
    let items = state.0.lock().map_err(|e| e.to_string())?;
    let Some(items) = items.as_ref() else {
        return Ok(false);
    };

    let rename = |item: &MenuItem<tauri::Wry>, text: String| -> Result<(), String> {
        item.set_text(text).map_err(|e| e.to_string())
    };
    rename(&items.dictate, labels.dictate)?;
    rename(&items.history, labels.history)?;
    rename(&items.settings, labels.settings)?;
    rename(&items.quit, labels.quit)?;

    if let Some(tray) = app.tray_by_id("murmure") {
        let _ = tray.set_tooltip(Some(labels.tooltip));
    }
    Ok(true)
}

fn build_tray(app: &AppHandle) -> tauri::Result<()> {
    // Libelles anglais a la construction : c'est la langue par defaut de
    // l'interface. Si l'utilisateur a choisi le francais, `set_tray_labels` les
    // remplace des que la fenetre principale a fini de se charger.
    let dictate = MenuItem::with_id(app, "dictate", "Dictate", true, None::<&str>)?;
    let history = MenuItem::with_id(app, "history", "History", true, None::<&str>)?;
    let settings = MenuItem::with_id(app, "settings", "Settings", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&dictate, &history, &settings, &quit])?;

    app.manage(TrayMenu(Mutex::new(Some(TrayItems {
        dictate: dictate.clone(),
        history: history.clone(),
        settings: settings.clone(),
        quit: quit.clone(),
    }))));

    TrayIconBuilder::with_id("murmure")
        .icon(app.default_window_icon().unwrap().clone())
        .tooltip("Murmure — local dictation")
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
        // EN PREMIER, avant tout autre plugin. Une seconde instance doit mourir
        // avant d'avoir enregistre quoi que ce soit : sans ca, deux icones dans
        // la zone de notification, deux fenetres, et deux applications qui se
        // disputent le meme raccourci global — la seconde le vole a la premiere,
        // qui devient silencieuse.
        //
        // Le second lancement n'est pas une erreur pour autant : c'est
        // quelqu'un qui veut voir Murmure. On montre donc la fenetre de
        // l'instance en place, exactement comme un clic sur l'icone.
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            open_main(app.clone(), None);
        }))
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
        .invoke_handler(tauri::generate_handler![
            show_overlay,
            hide_overlay,
            open_audio_folder,
            resize_overlay,
            open_main,
            trigger_dictation,
            set_hotkey,
            hotkey_status,
            set_tray_labels,
            type_text
        ])
        .setup(|app| {
            let handle = app.handle().clone();

            // Ici, et pas en argument de `.manage()` dans la chaine : les
            // arguments du builder sont evalues AVANT que les plugins ne
            // s'initialisent. Une seconde instance sondait donc le port avant
            // de mourir — inutile, et 600 ms de latence sur un lancement dont
            // on sait deja qu'il n'aboutira pas.
            app.manage(ServiceProcess(Mutex::new(spawn_service())));

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
