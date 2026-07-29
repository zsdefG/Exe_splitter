将 exe 拆分为多个分片，并生成可自动合并运行的启动器脚本。

positional arguments:
  exe_path              要拆分的 exe 文件路径

options:
  -h, --help            show this help message and exit
  --chunk-size CHUNK_SIZE
                        每个分片大小 (MB), 默认 10
  --output-dir OUTPUT_DIR
                        输出目录, 默认在 exe 同级目录下创建 _parts 文件夹
以上全部是 Python 内置模块，只要机器上有 Python 3.6+ 就能直接运行，无需 pip install 任何第三方包（应该是吧...）
