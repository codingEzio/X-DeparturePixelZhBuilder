# DeparturePixelZhBuilder

[English](README.md) · [简体中文](README.zh-Hans.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

バージョン管理されたレシピからDeparturePixelZhとDeparturePixelZh Compactを生成します。英字と中国語のピクセル字形、開発者向けアイコンを一つの等幅フォントにまとめます。

[DeparturePixelZh 0.1.0フォントをダウンロード（ZIP）](https://github.com/codingEzio/DeparturePixelZh/releases/download/v0.1.0/DeparturePixelZh-0.1.0.zip) · [すべてのリリース](https://github.com/codingEzio/DeparturePixelZh/releases)

![DeparturePixelZh — 英字と中国語のピクセル字形を一つの等幅フォントに](assets/departurepixelzh-social-card.png)

## はじめに

[uv](https://docs.astral.sh/uv/)をインストールし、DeparturePixelZhのフォントリポジトリを本リポジトリと同じ親ディレクトリに置きます。リリース用ビルドでは、フォントのレシピが指定するビルダーのリビジョンを使ってください。

```sh
uv run departurepixelzh-builder build --recipe ../DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../DeparturePixelZh/recipe.json --output Build --release
uv run -m unittest discover -s tests
```

ビルダーをローカル開発する場合は、`build`に`--development`を追加できます。リビジョンの固定を省略するため、リリース候補は生成しません。

## 処理内容

レシピは出典URLとSHA-256を固定します。ビルダーはそのURLから入力をダウンロードするか、キャッシュを再利用し、固定されたSHA-256で検証します。macOSにインストール済みのフォントをビルド入力として読み込むことはありません。ビルダーは字形を選び、共通のグリッドに合わせ、TTF、WOFF2、収録範囲、チェックサム、出典記録を生成します。英字とアイコンは1セル、全角文字は2セルです。Compactはより狭いセルを使います。絵文字はシステムの代替フォントに任せます。

字形の選択と間隔については[docs/how-it-works.md](docs/how-it-works.md)を参照してください。

## その他のコマンド

- `install`：検証済みフォントをインストールし、記録とバックアップを保存します。中断された処理は、次のインストール前に元に戻します。
- `check-installed`：インストール記録と実際のファイルを照合します。
- `sync`：利用側のマニフェストに従い、フォント、ライセンス文書、出典記録をコピーします。管理対象のファイルが外部で変更されている場合は停止します。
- `package`：検証済みの出力をアーカイブにします。

引数は`uv run departurepixelzh-builder --help`または各コマンドの`--help`で確認できます。インストールと同期は、想定外の変更を検出すると停止し、明示的な復旧を求めます。

## 任意：元のフォントと比較する

Homebrewを導入したmacOSでは、元のフォントをインストールして見た目を比較できます。これはビルドの前提条件ではなく、DeparturePixelZhのインストールや利用にも不要です。

```sh
brew install --cask font-departure-mono font-cubic-11
```

## 範囲とライセンス

DeparturePixelZhのレシピ専用のビルダーです。汎用フォントエディターではなく、太字、斜体、カラー絵文字は追加しません。

ビルダーの独自コードは[MIT](LICENSE-MIT)です。生成フォントには別途OFLとコンポーネントのライセンス条件が適用されます。フォントの再配布時には、フォントリポジトリのライセンス文書を保持してください。
