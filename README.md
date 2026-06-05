# 公共事業入札案件調査

公共事業・調達案件を毎日取得し、`index.html`で一覧確認するための最小構成です。主な取得元は中小企業庁の「官公需情報ポータルサイト検索API」とJETROの「政府公共調達データベース」です。

## ファイル構成

- `index.html` - 入札案件の一覧画面
- `data/bids.json` - HTMLが読み込む案件データ
- `config/sources.json` - API条件、対象省庁、対象都道府県コード、土木工事系の除外キーワード
- `config/target_urls.json` - 追加で直接読み取るURL一覧
- `scripts/collect_bids.py` - 取得処理
- `scripts/run_now.ps1` - 手動取得
- `scripts/register_daily_task.ps1` - Windowsタスクスケジューラ登録
- `scripts/serve.ps1` - ローカル表示用サーバー

## 手動取得

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_now.ps1
```

## HTMLを開く

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\serve.ps1 -Port 8000
```

その後、ブラウザで `http://localhost:8000/` を開きます。

## 毎日午前1時に取得

初期設定は午前1時です。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -Time 01:00
```

登録後はWindowsの「タスク スケジューラ」で `PublicBidCollector` を確認できます。

## GitHubで完全自動化する

GitHubにアップする場合は、`.github/workflows/collect-bids.yml` も含めてアップロードします。このワークフローは毎日午前1時ごろ（日本時間）にGitHub Actions上で取得処理を実行し、画面表示用の `data/bids.json` に差分がある場合だけ自動コミットします。

アップロードする主なファイルは次のとおりです。

- `index.html`
- `README.md`
- `.gitignore`
- `.github/workflows/collect-bids.yml`
- `config/sources.json`
- `config/target_urls.json`
- `scripts/collect_bids.py`
- `data/bids.json`

GitHub側で確認すること:

- Repository Settings > Actions > General > Workflow permissions を `Read and write permissions` にする
- Actionsタブで `Collect bid data` を `Run workflow` から手動実行できることを確認する
- GitHub Pagesを使う場合は、Pagesの公開元をリポジトリのルートに設定する

HTML画面の `最新データ再読込` は、取得済みの `data/bids.json` を読み直すボタンです。GitHub Pagesは静的サイトのため、画面上のボタンから安全にGitHub Actionsを直接起動することはできません。すぐ取得したい場合は、GitHubのActionsタブで `Collect bid data` を選び、`Run workflow` を押します。

## 取得元の調整

`config/sources.json` の `sources` で取得条件を調整できます。

- 5県は指定された官公需情報ポータルURLを `mode: "kkj_result_url"` として登録しています。
- URL内の県コード `pr=15,16,17,18,21` を読み取り、官公需情報ポータルAPIに県別で問い合わせます。
- 全省庁は `organization_names` に主要府省庁名を列挙しています。
- JETROは `mode: "jetro_local"` として、新潟県、富山県、石川県、福井県、岐阜県の県・政令指定都市・地方独立行政法人コードを指定して取得します。
- 取得対象期間は `api_issue_date_days_back` で調整できます。
- `exclude_categories` に含めた案件種別（例: `委託`、`物品`、`その他`）は通常の取得対象から外します。

土木工事系を除外する語句は `civil_engineering_exclude_keywords` にまとめています。自社で扱える・扱えない範囲に合わせて増減してください。

アスベスト・石綿系の案件は工事や解体に分類されることがあるため、`priority_include_keywords` に該当する案件は除外カテゴリや土木工事系除外語に当たっても残します。

省庁の調達情報公開ページは `config/target_urls.json` に登録しています。サイト側の制限でHTML取得できない省庁がある場合でも、全省庁分は官公需情報ポータルAPIでも取得します。

## 指定URLを追加で読み取る

任意の入札ページや省庁・自治体ページを直接読み取らせたい場合は、`config/target_urls.json` の `sources` に追加します。追加したURLはローカル実行でもGitHub Actionsでも毎回読み取られ、ページ本文またはページ内リンクが案件らしい場合に `data/bids.json` に追加されます。

```json
{
  "sources": [
    {
      "id": "niigata-custom-001",
      "enabled": true,
      "url": "https://example.jp/bid-page.html",
      "prefecture": "新潟県",
      "agency": "発注機関名",
      "source_name": "指定URL：発注機関名",
      "case_type_hint": "委託"
    }
  ]
}
```

`case_type_hint` は空でも動きます。ページ名や本文から `物品`、`委託`、`役務`、`公募・プロポーザル` などを推定します。
