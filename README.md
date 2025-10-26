

Slack TechニュースBot「TechNewsRanker」
1. 概要
TechNewsRanker は、最新のTechニュースを自動で収集・分析し、ランキング形式でSlackに投稿するPython製のBotです。

毎朝8時（JST）にGitHub Actionsによって自動実行され、前日8時以降に公開されたニュースを収集。Gemini API (1.5 Flash) を利用して、各記事を「重要度・面白さ・影響度」の3軸でAIがスコアリングし、上位5件のランキングをSlackの指定チャンネルに通知します。

HelloAIメンバーが朝一番に、膨大なニュースから「読むべき重要な記事」だけを効率的に把握することを目的としています。

2. 主な機能
自動ニュース収集: 複数の指定RSSフィード（TechCrunch Japan, The Vergeなど）から最新記事を自動取得。

AIによる分析・スコアリング: Gemini APIが各記事を分析。

100字以内での簡潔な「要約」

「重要度」「面白さ」「影響度」の3軸でのスコアリング

ランキング化: 総合スコアに基づき、上位5件を選出。

Slack自動投稿: 毎朝8時に整形されたランキング結果をSlackに投稿。

低コスト運用: GitHub ActionsとGemini APIの無料枠のみで動作します。

Slack投稿例
🚀 *Tech News Ranking (10/26)* 🚀
前日AM8:00以降のトップ5ニュースです。
────────────────────

🥇 *<URL|OpenAIが新モデル「GPT-5 Turbo」発表>*
> 重要度: 5 | 面白さ: 4 | 影響度: 5
> 👉 LLM市場での覇権争いがさらに激化。

🥈 *<URL|Google、Geminiの無料APIを一般開放>*
> 重要度: 4 | 面白さ: 5 | 影響度: 4
> 👉 開発者がコストゼロでAIを利用可能に。

...
3. 技術スタック
言語: Python 3.11

AIモデル: Gemini 1.5 Flash

スケジューラー: GitHub Actions (cron)

通知: Slack (Incoming Webhook)

主要ライブラリ: google-generativeai, requests, feedparser, python-dotenv

4. セットアップ方法
A. ローカルでのテスト実行
リポジトリをクローン:

Bash

git clone https://github.com/YOUR_USERNAME/tech-news-bot.git
cd tech-news-bot
仮想環境の作成と有効化:

Bash

python -m venv .venv
source .venv/bin/activate  # (macOS/Linux)
# .\.venv\Scripts\activate   # (Windows)
必要なライブラリをインストール:

Bash

pip install -r requirements.txt
.env ファイルの作成: main.py と同じ階層に .env ファイルを作成し、以下のキーを設定します。

コード スニペット

SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
GEMINI_API_KEY="AIzaSy..."
テスト実行:

Bash

python main.py
B. GitHub Actionsでの本番デプロイ (自動実行)
このBotは、リポジトリをGitHubにプッシュするだけで自動実行されるように設定されています（.github/workflows/main.yml）。

必要な設定は、以下の2つのシークレットをGitHubリポジトリに登録することだけです。

GitHubリポジトリの Settings > Secrets and variables > Actions を開きます。

New repository secret ボタンを押し、以下の2つを登録します。

Name: SLACK_WEBHOOK_URL

Value: (あなたのSlack Webhook URL)

Name: GEMINI_API_KEY

Value: (あなたのGemini APIキー)

これで設定は完了です。 .github/workflows/main.yml の cron 設定に基づき、毎日AM8:00 (JST) にBotが自動実行されます。 （Actionsタブから workflow_dispatch による手動実行も可能です）

5. 今後の拡張案 (PRDより)
チャンネルごとにテーマ（AI／Startup／Tech Policy）を設定可能にする。

ユーザーの関心分野を学習して個別最適化。

Geminiによる英語記事→日本語翻訳機能を追加。

（実装案）RSSではなく、記事本文のスクレイピングによる分析精度の向上。
