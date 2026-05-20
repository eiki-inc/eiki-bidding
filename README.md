# 公共事業入札案件調査

公共事業・調達案件を毎日取得し、`index.html`で一覧確認するための最小構成です。主な取得元は中小企業庁の「官公需情報ポータルサイト検索API」です。

## ファイル構成

- `index.html` - 入札案件の一覧画面
- `data/bids.json` - HTMLが読み込む案件データ
- `data/bids.csv` - Excel確認用のCSV出力
- `config/sources.json` - API条件、対象省庁、対象都道府県コード、土木工事系の除外キーワード
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

## 毎日午前1時から2時の間に取得

初期設定は午前1時30分です。

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\register_daily_task.ps1 -Time 01:30
```

登録後はWindowsの「タスク スケジューラ」で `PublicBidCollector` を確認できます。

## 取得元の調整

`config/sources.json` の `sources` で取得条件を調整できます。

- 対象5県は `lg_code: "15,16,17,18,21"` で指定しています。
- 全省庁は `organization_names` に主要府省庁名を列挙しています。
- 取得対象期間は `api_issue_date_days_back` で調整できます。
- `exclude_categories` の `"工事"` を外すと工事カテゴリも取得対象になります。

土木工事系を除外する語句は `civil_engineering_exclude_keywords` にまとめています。自社で扱える・扱えない範囲に合わせて増減してください。
