import re


def log(*args):
    print(*args, flush=True)


def normalize_text(s: str) -> str:
    s = (s or "").strip()
    s = s.replace("ي", "ی").replace("ك", "ک")
    s = s.replace("\u200c", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def normalize_match_text(s: str) -> str:
    s = normalize_text(s).lower()
    s = re.sub(r"[^\w\u0600-\u06FF ]+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def build_name_candidates(user: dict):
    first_name = normalize_match_text(user.get("first_name") or "")
    last_name = normalize_match_text(user.get("last_name") or "")
    username = normalize_match_text(user.get("username") or "")

    full_name = normalize_match_text(f"{first_name} {last_name}")
    candidates = set()

    if first_name:
        candidates.add(first_name)
    if last_name:
        candidates.add(last_name)
    if full_name:
        candidates.add(full_name)
    if username:
        candidates.add(username)
        candidates.add(username.lstrip("@"))
    if first_name and last_name:
        candidates.add(normalize_match_text(first_name + last_name))

    return sorted(c for c in candidates if c)


def name_matches(saved_name: str, user: dict) -> bool:
    saved = normalize_match_text(saved_name)
    if not saved:
        return False

    for c in build_name_candidates(user):
        if saved == c or saved in c or c in saved:
            return True
    return False


def get_best_display_name(user: dict) -> str:
    first_name = normalize_text(user.get("first_name") or "")
    last_name = normalize_text(user.get("last_name") or "")
    username = normalize_text(user.get("username") or "")

    full_name = normalize_text(f"{first_name} {last_name}")
    if full_name:
        return full_name
    if first_name:
        return first_name
    if username:
        return username
    return "کاربر"


def extract_gif_file_id_from_message(message: dict):
    animation = message.get("animation")
    if isinstance(animation, dict):
        file_id = animation.get("file_id") or animation.get("id")
        if file_id:
            return file_id

    document = message.get("document")
    if isinstance(document, dict):
        mime_type = (document.get("mime_type") or "").lower()
        file_name = (document.get("file_name") or "").lower()
        file_id = document.get("file_id") or document.get("id")

        if file_id and (
            "gif" in mime_type or
            file_name.endswith(".gif") or
            mime_type == "video/mp4"
        ):
            return file_id

    return None


def escape_md(text: str) -> str:
    if text is None:
        return ""
    for ch in ["_", "*", "`", "["]:
        text = text.replace(ch, f"\\{ch}")
    return text


def sanitize_bale_mention_name(name: str) -> str:
    name = normalize_text(name or "کاربر")
    for ch in ["(", ")", "[", "]", "<", ">"]:
        name = name.replace(ch, "")
    return name or "کاربر"


def build_bale_mention(user_id: int, name: str) -> str:
    # فرمت خامی که فعلاً برای بله استفاده می‌کنیم
    # و نباید escape شود:
    # (Arian)[uid:1784613415]
    return f"({sanitize_bale_mention_name(name)})[uid:{int(user_id)}]"