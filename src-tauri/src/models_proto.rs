//! Протокол `ayamodels://` — окно читает Live2D-модели прямо из ~/.aya/models,
//! не дожидаясь, пока поднимется ядро. На Windows адрес выглядит так:
//! `http://ayamodels.localhost/hiyori/hiyori.model3.json` — относительные пути
//! внутри model3.json (текстуры, moc3, движения) разрешаются сами.
use std::path::{Component, Path, PathBuf};

pub const SCHEME: &str = "ayamodels";

pub fn models_dir() -> Option<PathBuf> {
    let home = std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME"))?;
    Some(PathBuf::from(home).join(".aya").join("models"))
}

fn percent_decode(s: &str) -> Option<String> {
    let b = s.as_bytes();
    let mut out = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        if b[i] == b'%' {
            let hex = s.get(i + 1..i + 3)?;
            out.push(u8::from_str_radix(hex, 16).ok()?);
            i += 3;
        } else {
            out.push(b[i]);
            i += 1;
        }
    }
    String::from_utf8(out).ok()
}

/// Путь из адреса → файл внутри base. Никаких `..`, абсолютных путей и дисков.
pub fn resolve(base: &Path, uri_path: &str) -> Option<PathBuf> {
    let rel = percent_decode(uri_path.split(['?', '#']).next().unwrap_or(""))?;
    let rel = rel.trim_start_matches('/');
    if rel.is_empty() || rel.contains('\\') || rel.contains(':') {
        return None;
    }
    let rel = Path::new(rel);
    if !rel.components().all(|c| matches!(c, Component::Normal(_))) {
        return None;
    }
    Some(base.join(rel))
}

pub fn content_type(path: &Path) -> &'static str {
    match path.extension().and_then(|e| e.to_str()).map(|e| e.to_ascii_lowercase()).as_deref() {
        Some("json") => "application/json",
        Some("png") => "image/png",
        Some("jpg") | Some("jpeg") => "image/jpeg",
        Some("webp") => "image/webp",
        _ => "application/octet-stream",
    }
}

pub fn respond(uri_path: &str) -> tauri::http::Response<Vec<u8>> {
    let builder = tauri::http::Response::builder().header("Access-Control-Allow-Origin", "*");
    let file = models_dir().and_then(|base| resolve(&base, uri_path));
    match file.and_then(|p| std::fs::read(&p).ok().map(|bytes| (p, bytes))) {
        Some((p, bytes)) => builder
            .status(200)
            .header("Content-Type", content_type(&p))
            .header("Cache-Control", "no-cache")
            .body(bytes)
            .unwrap(),
        None => builder.status(404).body(Vec::new()).unwrap(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn resolves_inside_models_dir() {
        let base = Path::new("C:/m");
        assert_eq!(resolve(base, "/hiyori/a.model3.json"), Some(base.join("hiyori/a.model3.json")));
        assert_eq!(resolve(base, "/%D0%BC%D0%B8/tex%20a.png"), Some(base.join("ми/tex a.png")));
        assert_eq!(resolve(base, "/hiyori/a.json?x=1"), Some(base.join("hiyori/a.json")));
    }

    #[test]
    fn rejects_escapes() {
        let base = Path::new("C:/m");
        for bad in ["/../aya.db", "/a/../../x", "/%2e%2e/x", "/C:/Windows/x", "/a\\..\\x", "/", "", "/%zz"] {
            assert_eq!(resolve(base, bad), None, "{bad}");
        }
    }

    #[test]
    fn content_types() {
        assert_eq!(content_type(Path::new("a.model3.json")), "application/json");
        assert_eq!(content_type(Path::new("t.PNG")), "image/png");
        assert_eq!(content_type(Path::new("m.moc3")), "application/octet-stream");
    }
}
