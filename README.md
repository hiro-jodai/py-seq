# PI-SEQ — Raspberry Pi MIDI ステップシーケンサー

余ったラズパイで作る、**16ステップ × 最大32小節**の MIDI ステップシーケンサー。
GUI はブラウザ。バイブコーディング＋オープンソースだけで作っていくプロジェクト。

## できること (v0.2)

- 4トラック × 16ステップ × 最大32小節
- **4パターン**（P1-P4）ワンクリック切替、パターンごとに小節数も独立
- **ソングモード**：16エントリの曲順にパターンを並べて一続きの曲として再生
- **スイング**（裏拍の遅延 0-100%）
- ステップごとの**発音確率** (0-100%) ← IDMの心臓
- **ランダムノート**（トラックごとにスケール指定、RNDモード）
- **ヒューマナイズ**（タイミング・ベロシティの揺らぎ）
- **パラメータ記録再生**（REC onのまま再生中にツマミを動かすとオートメーション録音、ループ再生）
- 🎲 パターンランダマイズ
- MIDI出力（USB MIDI直挿し / 仮想ポート）

## 必要なもの

- Raspberry Pi（3以降、Zero 2 Wでも動くはず）
- ブラウザ（Piの画面でも、同じLANのPC/スマホからでもOK）
- MIDI音源（USB接続シンセ / PCのDAW / なくても仮想ポートで動く）

## セットアップ

```bash
# ラズパイ（Debian系）で最初にこれ
sudo apt update && sudo apt install -y python3-venv libasound2-dev build-essential git

git clone <repo-url> pi-seq
cd pi-seq
./setup.sh
```

## 起動

```bash
.venv/bin/python run.py
# → http://<ラズパイのIP>:8000 をブラウザで開く
```

MIDIポートを明示したい場合:

```bash
.venv/bin/python -c "import mido; print(mido.get_output_names())"
.venv/bin/python run.py --midi-port "UM-ONE MIDI 1"
```

送信中のMIDIを覗き見:

```bash
.venv/bin/python scripts/midi_monitor.py
```

## 使い方

- **クリック** でステップ on/off
- **P1-P4** でパターン切替（パターンごとに別のステップデータ）
- **SONG行** のセルをクリック → そのエントリのパターンを選択（1→2→3→4→1…）
- **SONG** ボタンでソングモード on/off（曲順に沿ってパターンが切り替わる）
- **SWING** スライダーで裏拍の遅延量（50% が王道シャッフル）
- **PROB モード**（ヘッダーのPROBボタン）ONにしてクリック → そのステップの発音確率をスライダー値に設定（0% で消去）
- **REC** onのまま再生中に BPM / velocity / 確率 / note を触るとオートメーション録音（CLR AUT で消去）
- **スペース** で再生/停止、**← →** で小節移動
- 各トラックの **FX/RND** で固定ノート ⇔ スケールランダム切り替え、🎲 でパターン乱数生成

## 構成

```
run.py                 エントリポイント
sequencer/core.py      シーケンスエンジン（スケジューラ・MIDI出力・オートメーション）
sequencer/web.py       WebSocket制御 + 静的ファイル配信
web/                   ブラウザUI（素のJS、ビルド不要）
scripts/midi_monitor.py  送信MIDIのモニタ
```

## ロードマップ

- [x] ソフト v0.1（16×32ステップ、確率、ランダムノート、ヒューマナイズ、オートメーション）
- [x] ソフト v0.2（スイング、4パターン切替、ソングモード）
- [ ] パターン保存/読み込み（JSON）、コピー/クリア
- [ ] ベロシティ perステップ
- [ ] MIDIクロック出力（外部シンセ同期）
- [ ] 入力グリッド（キースイッチ32個 Launchpad配置）→ QMK マクロパッド or GPIO
- [ ] ロータリーエンコーダ（I2C 拡張で16個狙う）
- [ ] PCB 化（KiCad + JLCPCB、AIで設計）
- [ ] Sovol で筐体印刷

## ライセンス

MIT
