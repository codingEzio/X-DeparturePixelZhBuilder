# X-DeparturePixelZhBuilder

[English](README.md) · [简体中文](README.zh-Hans.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

按版本化配方构建 DeparturePixelZh 和 DeparturePixelZh Compact，把英文像素字、中文像素字和开发者图标合进一款等宽字体。

[下载 DeparturePixelZh 0.1.0 字体包（ZIP）](https://github.com/codingEzio/X-DeparturePixelZh/releases/download/v0.1.0/DeparturePixelZh-0.1.0.zip) · [所有版本](https://github.com/codingEzio/X-DeparturePixelZh/releases)

![DeparturePixelZh：一款字体，中英像素等宽](assets/departurepixelzh-social-card.png)

## 开始

安装 [uv](https://docs.astral.sh/uv/)，把 DeparturePixelZh 字体仓库放在本仓库的同级目录。发布构建请使用字体配方锁定的构建器版本。

```sh
uv run departurepixelzh-builder build --recipe ../X-DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../X-DeparturePixelZh/recipe.json --output Build --release
uv run -m unittest discover -s tests
```

本地开发构建器时，可为 `build` 添加 `--development`。它会跳过版本锁定，不生成发布候选。

## 它做什么

配方固定来源 URL 和 SHA-256。构建器从这些 URL 下载输入文件，或复用缓存，并按固定的 SHA-256 校验；不会读取 macOS 已安装字体作为构建输入。构建器选择字形、适配到共同网格，再生成 TTF、WOFF2、字符覆盖表、校验和及来源记录。英文和图标占一格，全角字符占两格；Compact 使用更窄的格子。emoji 仍使用系统备用字体。

字形选择与间距说明见 [docs/how-it-works.md](docs/how-it-works.md)。

## 其他命令

- `install`：安装验证过的字体，保存记录与备份。安装中断后，下次安装会先回滚未完成操作。
- `check-installed`：按安装记录检查已安装文件。
- `sync`：按使用方清单复制字体、许可和来源记录。受管文件在工具之外被修改时，会停止操作。
- `package`：把检查过的产物打包。

参数见 `uv run departurepixelzh-builder --help` 或各命令的 `--help`。安装和同步遇到意外修改会停止，让恢复操作得到明确处理。

## 可选：对比原版字体

在装有 Homebrew 的 macOS 上，可以安装原版字体做视觉对比。这不是构建前提，安装或使用 DeparturePixelZh 也不需要这一步。

```sh
brew install --cask font-departure-mono font-cubic-11
```

## 范围与许可

这是 DeparturePixelZh 配方的专用构建器，不是通用字体编辑器，也不添加粗体、斜体或彩色 emoji。

构建器原创代码使用 [MIT](LICENSE-MIT)。生成字体另有 OFL 和组件许可要求；再分发字体时应保留字体仓库中的许可材料。
