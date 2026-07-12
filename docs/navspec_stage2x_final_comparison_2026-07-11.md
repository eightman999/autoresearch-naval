# navspec Stage 2.x 最終比較

作成日: 2026-07-12

評価契約 `navspec-stage2x-eval-contract-v1` を使用。正式比較は strict reason exact と semantic reason micro F1 を使用し、legacy reason exact は連続性確認用です。

| checkpoint | status | schema | impossible F1 | legacy exact | strict exact | semantic micro F1 | primary | extra | missing | invalid | field adoption | truncated | early EOS | speed strict | era strict | 622 semantic | 採用 |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|
| stage2-iter1000 | accepted | 87.5% | 99.5% | 90.2% | 78.1% | 94.7% | 94.1% | 4.6% | 11.9% | 0.0% | 20.0% | 0.0% | 0 | 43.2% | 65.5% | 81.4% | accepted_default |
| stage2.1-iter500 | experimental | 87.5% | 99.5% | 88.1% | 79.8% | 95.2% | 99.2% | 8.1% | 6.8% | 0.0% | 21.6% | 0.0% | 0 | 30.0% | 64.2% | 80.6% | experimental_only |
| stage2.2-iter500 | experimental_rejected | 93.8% | 97.1% | 81.2% | 81.2% | 91.8% | 86.8% | 8.1% | 16.2% | 0.0% | 21.7% | 6.2% | 30 | 100.0% | 60.0% | 87.4% | experimental_rejected |
| stage2.3-iter250 | experimental_rejected | 93.5% | 99.5% | 75.8% | 75.8% | 89.0% | 92.7% | 25.7% | 10.8% | 2.2% | 21.5% | 0.0% | 0 | 85.0% | 88.5% | 85.2% | experimental_rejected_representative |
| stage2.3-iter500 | experimental_rejected | 90.4% | 99.5% | 76.2% | 75.6% | 89.4% | 95.4% | 25.1% | 8.9% | 3.8% | 21.6% | 0.0% | 0 | 85.0% | 85.5% | 89.2% | experimental_rejected_comparison |

## 決定

- Stage 2 iter1000をaccepted/defaultとして維持する。
- Stage 2.1はexperimental、Stage 2.2/2.3はexperimental_rejectedとして保存する。
- Stage 2.4は実施せず、Stage 3はStage 2 iter1000 + classic validatorをbaselineとする。

## 保存

- ケース別評価JSONは再生成可能なためGit管理外。要約JSON、contract manifest、hash一覧、adapter metadataと本レポートをGit管理する。
- LoRA重みはGit LFS管理。正式reason codeはclassic rule engineを正とする。
