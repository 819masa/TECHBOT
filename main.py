"""Slack notification helper script."""

import argparse
import json
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Final

import feedparser
import google.generativeai as genai
import requests
from dotenv import load_dotenv

# 取得対象のRSSフィードURLリスト
RSS_FEEDS = {
    # TechCrunch Japan (jp.techcrunch.com) の /feed/
    "TechCrunch Japan": "https://jp.techcrunch.com/feed/",     

    # Publickey の Atom フィード
    "Publickey": "https://www.publickey1.jp/atom.xml",

    # CodeZine の新着記事フィード
    "CodeZine": "https://codezine.jp/rss/new/index.xml",

    # Gihyo Digital Publishing の RSS フィード
    "Gihyo Digital Publishing": "https://gihyo.jp/dev/feed/rss2",

    # ITmedia News の RSS フィー
    "ITmedia News": "https://rss.itmedia.co.jp/rss/2.0/news_bursts.xml"
}

_ANNOUNCED_GEMINI_MODELS: set[str] = set()

_GADGET_KEYWORDS = [
    "iphone",
    "ipad",
    "ipod",
    "apple watch",
    "macbook",
    "airpods",
    "pixel",
    "galaxy",
    "androidスマホ",
    "スマートフォン",
    "スマホ",
    "ガジェット",
    "デジカメ",
    "家電",
    "ヘッドフォン",
    "イヤホン",
    "セール",
    "sale",
    "割引",
    "値下げ",
    "タイムセール",
    "amazon",
    "楽天",
]

# --- Step 3: News Fetching ---

def _is_gadget_news(*texts: str) -> bool:
    haystack = " ".join(filter(None, texts)).lower()
    return any(keyword in haystack for keyword in _GADGET_KEYWORDS)


def get_recent_news() -> list[dict]:
    """
    RSS_FEEDSから「前日AM8時(JST)以降」の記事を取得する
    """
    print("Fetching recent news...")
    
    # 1. タイムゾーンと言「昨日AM8時」を定義
    JST = timezone(timedelta(hours=+9), 'JST')
    now = datetime.now(JST)
    
    # 実行時刻がAM8時より前か後かで「昨日」の定義を変える
    if now.hour < 8:
        # AM8時より前に実行された場合 (例: 10/26 AM7:00)
        # -> 10/24 AM8:00 以降が対象
        yesterday_8am = (now - timedelta(days=2)).replace(hour=8, minute=0, second=0, microsecond=0)
    else:
        # AM8時以降に実行された場合 (例: 10/26 AM9:00)
        # -> 10/25 AM8:00 以降が対象
        yesterday_8am = (now - timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)

    print(f"Targeting news published after: {yesterday_8am.isoformat()}")

    recent_articles = []
    
    for source_name, url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(url)
            
            for entry in feed.entries:
                # 2. 記事の公開日時を取得
                # feedparserは自動でUTCに変換してくれる (struct_time)
                published_time_utc = datetime.fromtimestamp(
                    time.mktime(entry.published_parsed), timezone.utc
                )
                
                # 3. JSTの「昨日AM8時」と比較
                if published_time_utc > yesterday_8am:
                    title = entry.title
                    summary_text = getattr(entry, "summary", "")
                    if _is_gadget_news(title, summary_text):
                        continue
                    article = {
                        "title": title,
                        "url": entry.link,
                        "published_utc": published_time_utc,  # (デバッグ用)
                        "source": source_name,
                        "summary": summary_text,
                    }
                    recent_articles.append(article)
                    
        except Exception as e:
            print(f"Error fetching RSS from {source_name}: {e}")

    print(f"Found {len(recent_articles)} recent articles.")
    return recent_articles


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`").strip()
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].lstrip()
    if stripped.endswith("```"):
        stripped = stripped[: -3].rstrip()

    start = stripped.find("{")
    if start == -1:
        raise ValueError("Gemini response did not contain a JSON object.")
    depth = 0
    for index in range(start, len(stripped)):
        char = stripped[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return stripped[start : index + 1]

    raise ValueError("Gemini response contained an incomplete JSON object.")


def _resolve_gemini_model(preferred: str | None) -> str:
    candidate = (preferred or os.getenv("GEMINI_MODEL") or "").strip()
    if not candidate:
        raise RuntimeError(
            "GEMINI_MODEL environment variable is not set. "
            "Set it in your .env file to the Gemini model you want to use (e.g., models/gemini-1.5-flash-latest)."
        )

    # ユーザーが短い名前を指定した場合は自動で `models/` を補完する
    if "/" not in candidate:
        candidate = f"models/{candidate}"

    return candidate


def _validate_analysis_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if "summary" not in payload or "scores" not in payload:
        raise ValueError("Gemini JSON must contain 'summary' and 'scores'.")

    summary = payload.get("summary", "")
    if not isinstance(summary, str):
        raise ValueError("Gemini 'summary' must be a string.")
    payload["summary"] = summary[:100]

    scores = payload.get("scores", {})
    if not isinstance(scores, dict):
        raise ValueError("Gemini 'scores' must be a JSON object.")

    required_scores = {
        "importance": "市場や技術トレンドへのインパクト",
        "interestingness": "新規性・驚き・HelloAIメンバーの関心度",
        "impact": "HelloAIメンバーが今知るべき影響",
    }

    for key in required_scores:
        value = scores.get(key)
        if not isinstance(value, int):
            raise ValueError(f"Gemini score '{key}' must be an integer between 1 and 5.")
        scores[key] = max(1, min(5, value))

    payload["scores"] = scores
    return payload


def _calculate_total_score(scores: dict[str, int]) -> int:
    return int(scores.get("importance", 0)) + int(scores.get("interestingness", 0)) + int(scores.get("impact", 0))


def analyze_article_with_gemini(
    article: dict[str, Any], *, model_name: str | None = None
) -> dict[str, Any]:
    """Return Gemini-generated summary (<=100 chars) and 3-axis scores for an article."""

    gemini_api_key = os.getenv("GEMINI_API_KEY")
    if not gemini_api_key:
        raise RuntimeError(
            "GEMINI_API_KEY environment variable is not set. "
            "Set it to your Google Gemini API key before calling analyze_article_with_gemini()."
        )

    genai.configure(api_key=gemini_api_key)
    resolved_model = _resolve_gemini_model(model_name or os.getenv("GEMINI_MODEL"))
    if resolved_model not in _ANNOUNCED_GEMINI_MODELS:
        print(f"Using Gemini model: {resolved_model}")
        _ANNOUNCED_GEMINI_MODELS.add(resolved_model)
    model = genai.GenerativeModel(resolved_model)

    published_iso = (
        article.get("published_utc").isoformat()
        if hasattr(article.get("published_utc"), "isoformat")
        else str(article.get("published_utc"))
    )

    prompt = f"""
あなたは技術系ニュースを分析するアシスタントです。以下の記事を読み、エンジニア向けに100字以内で要約し、3つの指標で5段階評価してください。JSONのみを返してください。

記事タイトル: {article.get("title", "")}
URL: {article.get("url", "")}
媒体: {article.get("source", "")}
公開日時(UTC): {published_iso}
記事概要・要約: {article.get("summary", "").strip() or "(本文概要なし)"}

---
   [3つの指標の採点基準]
   
   A. 重要度 (Importance): 市場や技術トレンド全体へのインパクト
     - 5: ゲームチェンジャー。市場の勢力図を塗り替えるレベル。
     - 4: 業界全体の常識やワークフローに大きな影響を与える。
     - 3: 競合が追随しそうな、注目すべき新機能や研究成果。
     - 2: 特定分野でのみ意味のある進展。
     - 1: 限定的・ニッチなアップデート。

   B. 面白さ (Interest): HelloAIメンバーの知的好奇心を刺激する度合い
     - 5: 常識を覆す。非常にクリエイティブで「Slackが盛り上がる」レベル。
     - 4: 「すごい発想」「そんなのアリ？」と議論のネタになる。
     - 3: 「おっ」と思うようなユニークな視点や新しいアイデア。
     - 2: 予想通りの順当な進化。
     - 1: 既知の情報、定期報告。

   C. 影響度 (Impact): HelloAIメンバーの「行動」に与える影響
     - 5: 最優先で対応必須。既存のプロジェクトや技術選定を見直す必要がある。
     - 4: すぐに試すべき。開発やビジネスアイデアに直接的なヒントを与える。
     - 3: 多くのメンバーが「キャッチアップすべき」技術・スキル。
     - 2: 特定分野のメンバーには関連するかも。
     - 1: 雑学・教養として知っておく程度。

出力フォーマット:
{{
  "summary": "100字以内の要約（日本語・エンジニア向け）",
  "scores": {{
    "importance": 1-5の整数,  # 市場や技術トレンドへのインパクト
    "interestingness": 1-5の整数,  # 新規性・驚き・HelloAIメンバーの関心度
    "impact": 1-5の整数  # HelloAIメンバーが今知るべき影響
  }}
}}

必ず数値は整数にし、余分な説明やテキストを含めないでください。
"""

    response = model.generate_content(prompt)
    response_text = getattr(response, "text", None) or getattr(response, "output_text", None)
    if not response_text:
        raise RuntimeError("Gemini returned an empty response.")

    json_text = _extract_json_object(response_text)
    try:
        payload = json.loads(json_text)
    except json.JSONDecodeError as exc:
        raise ValueError("Gemini response was not valid JSON.") from exc
    return _validate_analysis_payload(payload)


def format_slack_message(ranked_articles: list[dict[str, Any]]) -> str:
    """Ranked article list を PRD 指定の Slack メッセージ形式に整形する。"""

    JST = timezone(timedelta(hours=+9), "JST")
    today_str = datetime.now(JST).strftime("%m/%d")

    message_lines = [
        f"🚀 *Tech News Ranking ({today_str})* 🚀",
        "前日AM8:00以降のトップ5ニュースです。",
        "─" * 20,
    ]

    icons = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"]

    for index, article in enumerate(ranked_articles[: len(icons)]):
        title = article.get("title", "")
        url = article.get("url", "")
        analysis = article.get("analysis", {})
        summary = analysis.get("summary", "")
        scores = analysis.get("scores", {})

        clean_summary = summary.replace("\n", " ")

        line = f"{icons[index]} *<{url}|{title}>*\n"
        line += (
            f"> 重要度: {scores.get('importance', '?')} | "
            f"面白さ: {scores.get('interestingness', '?')} | "
            f"影響度: {scores.get('impact', '?')}\n"
        )
        line += f"> 👉 {clean_summary} \n"

        message_lines.append(line)

    return "\n".join(message_lines)


def post_to_slack(message: str) -> requests.Response:
    """Post ``message`` to Slack via an incoming webhook."""

    slack_webhook_url: Final[str | None] = os.getenv("SLACK_WEBHOOK_URL")

    if not slack_webhook_url:
        raise RuntimeError(
            "SLACK_WEBHOOK_URL environment variable is not set. "
            "Set it to your Slack incoming webhook URL before calling post_to_slack()."
        )

    payload = {"text": message}

    response = requests.post(slack_webhook_url, json=payload, timeout=10)
    response.raise_for_status()
    return response


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Fetch tech news and optionally send Slack updates.")
    parser.add_argument(
        "--send-slack",
        action="store_true",
        help="Send the formatted ranking message to Slack using the configured webhook.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="Number of articles to include in the Slack message (max 5).",
    )
    return parser.parse_args()


def main(args: argparse.Namespace) -> None:
    load_dotenv()

    # --- Step 3 Test ---
    articles = get_recent_news()

    if not articles:
        print("No new articles found.")

    gemini_enabled = bool(os.getenv("GEMINI_API_KEY"))
    if gemini_enabled:
        print("Gemini analysis is enabled. Running article summaries and scoring...")
    else:
        print("Gemini analysis is disabled. Set GEMINI_API_KEY to enable summaries and scoring.")

    analysis_results: list[dict[str, Any]] = []

    for i, article in enumerate(articles):
        print(f"--- Article {i + 1} ---")
        print(f"Title: {article['title']}")
        print(f"URL: {article['url']}")
        print(f"Source: {article['source']}")
        print(f"Published: {article['published_utc'].isoformat()}")  # UTCで表示
        if article.get("summary"):
            print(f"Feed Summary: {article['summary']}")

        if gemini_enabled:
            try:
                analysis = analyze_article_with_gemini(article)
                total_score = _calculate_total_score(analysis.get("scores", {}))
                result = {
                    "title": article["title"],
                    "url": article["url"],
                    "source": article["source"],
                    "published_utc": article["published_utc"].isoformat()
                    if hasattr(article["published_utc"], "isoformat")
                    else article["published_utc"],
                    "analysis": analysis,
                    "total_score": total_score,
                }
                analysis_results.append(result)
                print(json.dumps(result, ensure_ascii=False, indent=2))
            except Exception as err:
                print(f"Gemini analysis failed: {err}")

    if analysis_results:
        analysis_results.sort(key=lambda item: item.get("total_score", 0), reverse=True)
        print("Gemini analysis results (JSON array):")
        print(json.dumps(analysis_results, ensure_ascii=False, indent=2))

        slack_message = format_slack_message(analysis_results[: max(1, min(args.limit, 5))])
        print("\nSlack message preview:\n")
        print(slack_message)
        if args.send_slack:
            try:
                post_to_slack(slack_message)
                print("Slack message sent successfully.")
            except Exception as slack_err:
                print(f"Failed to send Slack message: {slack_err}")
    elif args.send_slack:
        print("No Gemini analysis results available; nothing to send to Slack.")

    # --- Step 2 Test (一時的にコメントアウト) ---
    # print("Sending test message to Slack...")
    # try:
    #     post_to_slack("TechNewsRanker 起動テスト")
    #     print("Test message sent successfully!")
    # except Exception as e:
    #     print(f"Error sending message: {e}")


if __name__ == "__main__":
    main(parse_args())