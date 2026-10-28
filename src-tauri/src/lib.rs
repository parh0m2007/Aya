use tauri::{
    menu::{CheckMenuItem, MenuBuilder, MenuItem, PredefinedMenuItem},
    tray::TrayIconBuilder,
    Emitter, Manager,
};

mod core_proc;
mod models_proto;
use core_proc::CoreProc;

struct TrayChecks {
    followup: CheckMenuItem<tauri::Wry>,
    focus: CheckMenuItem<tauri::Wry>,
}

#[tauri::command]
fn set_tray_check(
    checks: tauri::State<'_, TrayChecks>,
    id: String,
    checked: bool,
) -> Result<(), String> {
    let item = match id.as_str() {
        "followup" => &checks.followup,
        "focus" => &checks.focus,
        _ => return Err(format!("unknown tray check: {id}")),
    };
    item.set_checked(checked).map_err(|e| e.to_string())
}

pub fn run() {
    // Ядро стартует сразу, ещё до окон: пока поднимается WebView, Python уже грузится.
    let core = CoreProc::start();
    let core_exit = std::sync::Arc::clone(&core);
    tauri::Builder::default()
        .plugin(tauri_plugin_global_shortcut::Builder::new().build())
        .plugin(tauri_plugin_autostart::init(
            tauri_plugin_autostart::MacosLauncher::LaunchAgent,
            None,
        ))
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_process::init())
        // Модели аватара с диска — аватар появляется, не дожидаясь ядра
        .register_uri_scheme_protocol(models_proto::SCHEME, |_ctx, request| {
            models_proto::respond(request.uri().path())
        })
        .invoke_handler(tauri::generate_handler![set_tray_check])
        // Крестик консоли прячет окно, а не уничтожает: иначе «Открыть консоль»
        // в трее и кнопки в окне Аи после первого закрытия переставали работать.
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == "console" {
                    api.prevent_close();
                    let _ = window.hide();
                }
            }
        })
        .setup(|app| {
            // Обновления — только когда в tauri.conf.json задан открытый ключ подписи
            // (plugins.updater.pubkey): без него плагин не стартует, а приложение
            // должно работать и в сборке разработчика.
            let has_updater_key = app
                .config()
                .plugins
                .0
                .get("updater")
                .and_then(|u| u.get("pubkey"))
                .and_then(|k| k.as_str())
                .is_some_and(|k| !k.trim().is_empty());
            if has_updater_key {
                app.handle().plugin(tauri_plugin_updater::Builder::new().build())?;
            }
            // Окно создаём вручную: WebView2 по умолчанию блокирует любой
            // audio.play() без активного жеста в момент вызова, поэтому
            // ответ Аи голосом (стриминговый TTS через пару секунд после
            // нажатия) не звучал. no-user-gesture-required разрешает автоплей.
            // Плюс флаги против дросселирования таймеров в фоне: инициатива и
            // VAD должны работать, когда окно свёрнуто.
            for win in app.config().app.windows.iter().cloned() {
                let mut builder =
                    tauri::webview::WebviewWindowBuilder::from_config(app.handle(), &win)?;
                #[cfg(target_os = "windows")]
                {
                    builder = builder.additional_browser_args(
                        "--autoplay-policy=no-user-gesture-required \
                         --disable-background-timer-throttling \
                         --disable-backgrounding-occluded-windows \
                         --disable-renderer-backgrounding \
                         --disable-features=msWebOOUI,msPdfOOUI,msSmartScreenProtection",
                    );
                }
                builder.build()?;
            }

            // Трей: показать Аю / консоль / быстрые переключатели / выход
            let show = MenuItem::with_id(app, "show", "Показать Аю", true, None::<&str>)?;
            let console = MenuItem::with_id(app, "console", "Открыть консоль", true, None::<&str>)?;
            let sep1 = PredefinedMenuItem::separator(app)?;
            let followup = CheckMenuItem::with_id(
                app,
                "followup",
                "Свободные руки",
                true,
                false,
                None::<&str>,
            )?;
            let focus = CheckMenuItem::with_id(app, "focus", "Тишина", true, false, None::<&str>)?;
            let sep2 = PredefinedMenuItem::separator(app)?;
            let quit = MenuItem::with_id(app, "quit", "Выход", true, None::<&str>)?;
            let menu = MenuBuilder::new(app)
                .items(&[&show, &console, &sep1, &followup, &focus, &sep2, &quit])
                .build()?;
            app.manage(TrayChecks { followup, focus });
            TrayIconBuilder::with_id("aya-tray")
                .icon(app.default_window_icon().expect("no default icon").clone())
                .tooltip("Aya")
                .menu(&menu)
                .show_menu_on_left_click(true)
                .on_menu_event(|app, event| match event.id().as_ref() {
                    "show" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.unminimize();
                            let _ = w.show();
                            let _ = w.set_focus();
                        }
                    }
                    "console" => {
                        if let Some(c) = app.get_webview_window("console") {
                            let _ = c.unminimize();
                            let _ = c.show();
                            let _ = c.set_focus();
                        }
                    }
                    "followup" => {
                        let _ = app.emit_to("main", "tray-toggle-followup", ());
                    }
                    "focus" => {
                        let _ = app.emit_to("main", "tray-toggle-focus", ());
                    }
                    "quit" => app.exit(0),
                    _ => {}
                })
                .build(app)?;

            // «Показать/скрыть Аю» (по умолчанию Alt+Shift+A) регистрирует окно —
            // так сочетание можно поменять в консоли (src/main.ts, syncHotkeys).

            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(move |_app, event| {
            if let tauri::RunEvent::Exit = event {
                core_exit.stop();
            }
        });
    drop(core);
}
