import argparse
import base64
import datetime
import json
import os
import re
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key.strip(), value)


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if value is None or value == "":
        raise SystemExit(f"Missing env var: {name}")
    return value


def sanitize(name: str) -> str:
    clean = name.encode("ascii", "ignore").decode().strip()
    clean = re.sub(r"[^A-Za-z0-9._-]+", "_", clean).strip("._")
    return clean or "attachment.pdf"


def read_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def write_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n")


def gmail_service(secret_file: str, token_file: Path):
    secret_data = json.loads(Path(secret_file).read_text())
    if "installed" not in secret_data:
        raise SystemExit(
            "OAUTH_CLIENT_SECRET_FILE must point to a Google OAuth Desktop app JSON, not a web client JSON"
        )

    creds = None
    if token_file.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(token_file), SCOPES)
        except Exception:
            creds = None
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(secret_file, SCOPES)
        creds = flow.run_local_server(port=0)

    token_file.parent.mkdir(parents=True, exist_ok=True)
    token_file.write_text(creds.to_json())
    return build("gmail", "v1", credentials=creds)


def attachment_parts(payload: dict) -> list[tuple[str, str]]:
    found = []
    stack = payload.get("parts", [])[:]
    while stack:
        part = stack.pop()
        stack.extend(part.get("parts", []))
        filename = part.get("filename") or ""
        body = part.get("body") or {}
        attachment_id = body.get("attachmentId")
        if filename.lower().endswith(".pdf") and attachment_id:
            found.append((filename, attachment_id))
    return found


def target_path(
    output_dir: Path,
    internal_date: str,
    message_id: str,
    filename: str,
    attachment_id: str,
) -> Path:
    stem = f"{internal_date}_{message_id[:8]}_{sanitize(Path(filename).name)}"
    path = output_dir / stem
    if not path.exists():
        return path
    return output_dir / f"{path.stem}_{attachment_id[:8]}{path.suffix}"


def after_token(after_date: str | datetime.date | None) -> str | None:
    if after_date is None:
        return None
    if isinstance(after_date, datetime.date):
        parsed = after_date
    elif isinstance(after_date, str):
        parsed = datetime.date.fromisoformat(after_date.strip())
    else:
        raise TypeError("after_date must be YYYY-MM-DD string or datetime.date")
    return parsed.strftime("%Y/%m/%d")


def download_pdfs(after_date: str | datetime.date | None = None) -> int:
    load_env(Path("config/.env"))
    cutoff = after_token(after_date if after_date is not None else os.getenv("AFTER_DATE"))
    query = env("GMAIL_QUERY")
    if cutoff:
        query = f"{query} after:{cutoff}"
    output_dir = Path(env("OUTPUT_DIR")).expanduser()
    token_file = Path(env("TOKEN_FILE")).expanduser()
    state_file = Path(os.getenv("STATE_FILE") or output_dir / ".state.json").expanduser()
    secret_file = env("OAUTH_CLIENT_SECRET_FILE")
    max_results = max(1, int(os.getenv("MAX_RESULTS", "200")))
    dry_run = os.getenv("DRY_RUN", "0") == "1"
    output_dir.mkdir(parents=True, exist_ok=True)
    state = read_state(state_file)
    service = gmail_service(secret_file, token_file)

    downloaded = skipped = matched = 0
    page_token = None
    while matched < max_results:
        batch = min(500, max_results - matched)
        response = service.users().messages().list(
            userId="me", q=query, maxResults=batch, pageToken=page_token
        ).execute()
        messages = response.get("messages", [])
        if not messages:
            break
        matched += len(messages)
        for item in messages:
            message = service.users().messages().get(
                userId="me", id=item["id"], format="full"
            ).execute()
            internal_date = message["internalDate"]
            date_text = datetime.datetime.fromtimestamp(int(internal_date) / 1000).strftime(
                "%Y-%m-%d"
            )
            for filename, attachment_id in attachment_parts(message.get("payload", {})):
                key = f"{message['id']}:{attachment_id}"
                if key in state and Path(state[key]).exists():
                    skipped += 1
                    continue
                path = target_path(output_dir, date_text, message["id"], filename, attachment_id)
                if dry_run:
                    print(f"DRY_RUN {path.name}")
                    continue
                data = service.users().messages().attachments().get(
                    userId="me", messageId=message["id"], id=attachment_id
                ).execute()["data"]
                path.write_bytes(base64.urlsafe_b64decode(data))
                state[key] = str(path)
                downloaded += 1
                print(f"downloaded date={date_text} file={path.name}")
        page_token = response.get("nextPageToken")
        if not page_token:
            break

    if not dry_run:
        write_state(state_file, state)
    print(f"matched={matched} downloaded={downloaded} skipped={skipped}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("after_date", nargs="?", help="Fetch only emails after YYYY-MM-DD")
    args = parser.parse_args(argv)
    return download_pdfs(args.after_date)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(1)
