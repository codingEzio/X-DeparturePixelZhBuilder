# DeparturePixelZhBuilder

[English](README.md) · [简体中文](README.zh-Hans.md) · [日本語](README.ja.md) · [한국어](README.ko.md) · [Español](README.es.md)

버전이 관리되는 레시피로 DeparturePixelZh와 DeparturePixelZh Compact를 만듭니다. 영문과 중국어 픽셀 글자, 개발자 아이콘을 하나의 고정폭 글꼴로 조합합니다.

[DeparturePixelZh 0.1.0 글꼴 다운로드 (ZIP)](https://github.com/codingEzio/DeparturePixelZh/releases/download/v0.1.0/DeparturePixelZh-0.1.0.zip) · [모든 릴리스](https://github.com/codingEzio/DeparturePixelZh/releases)

![DeparturePixelZh — 영문과 중국어 픽셀 글자를 하나의 고정폭 글꼴에](assets/departurepixelzh-social-card.png)

## 시작

[uv](https://docs.astral.sh/uv/)를 설치하고 DeparturePixelZh 글꼴 저장소를 이 저장소와 같은 상위 디렉터리에 둡니다. 릴리스 빌드에는 글꼴 레시피에 고정된 빌더 리비전을 사용하세요.

```sh
uv run departurepixelzh-builder build --recipe ../DeparturePixelZh/recipe.json --output Build
uv run departurepixelzh-builder check --recipe ../DeparturePixelZh/recipe.json --output Build --release
uv run -m unittest discover -s tests
```

빌더를 로컬에서 개발할 때는 `build`에 `--development`를 추가할 수 있습니다. 리비전 고정을 건너뛰므로 릴리스 후보를 만들지 않습니다.

## 동작

레시피는 소스 URL과 SHA-256을 고정합니다. 빌더는 해당 URL에서 입력 파일을 다운로드하거나 캐시를 재사용하며, 고정된 SHA-256으로 검증합니다. macOS에 설치된 글꼴을 빌드 입력으로 읽지 않습니다. 빌더는 글자를 선택하고 공통 격자에 맞춘 뒤 TTF, WOFF2, 지원 범위, 체크섬, 출처 기록을 생성합니다. 영문과 아이콘은 한 칸, 전각 문자는 두 칸을 사용합니다. Compact는 더 좁은 칸을 사용합니다. 이모지는 시스템 대체 글꼴에 맡깁니다.

글자 선택과 간격은 [docs/how-it-works.md](docs/how-it-works.md)에서 설명합니다.

## 다른 명령

- `install`: 검증한 글꼴을 설치하고 기록과 백업을 저장합니다. 중단된 설치는 다음 설치를 진행하기 전에 되돌립니다.
- `check-installed`: 설치 기록과 실제 파일을 비교합니다.
- `sync`: 사용 프로젝트의 매니페스트에 따라 글꼴, 라이선스 문서, 출처 기록을 복사합니다. 관리 파일이 도구 외부에서 변경되었다면 중지합니다.
- `package`: 검사한 결과물을 아카이브로 만듭니다.

인수는 `uv run departurepixelzh-builder --help` 또는 각 명령의 `--help`를 참고하세요. 설치와 동기화는 예상하지 못한 변경을 발견하면 중지하여 복구를 명시적으로 처리하도록 합니다.

## 선택 사항: 원본 글꼴과 비교

Homebrew가 설치된 macOS에서는 원본 글꼴을 설치해 모양을 비교할 수 있습니다. 빌드의 필수 조건이 아니며, DeparturePixelZh를 설치하거나 사용하는 데도 필요하지 않습니다.

```sh
brew install --cask font-departure-mono font-cubic-11
```

## 범위와 라이선스

DeparturePixelZh 레시피를 위한 전용 빌더입니다. 범용 글꼴 편집기가 아니며 굵은 글꼴, 기울임꼴, 컬러 이모지를 추가하지 않습니다.

빌더의 원본 코드는 [MIT](LICENSE-MIT)를 사용합니다. 생성된 글꼴에는 별도의 OFL 및 구성 요소 라이선스 조건이 적용됩니다. 글꼴을 재배포할 때는 글꼴 저장소의 라이선스 문서를 유지하세요.
