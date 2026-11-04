# tests 说明

运行全部测试（纯标准库 `unittest`，离线，不依赖 `data/`、`FIN_Audit.csv` 与网络）：

```bash
python -m unittest discover -s tests -v
```

需要先可导入本包：在仓库根目录直接运行即可，或先 `pip install -e .`。
