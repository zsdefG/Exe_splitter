#!/usr/bin/env python3
"""
exe_splitter.py - 将 exe 拆分为多个加密分片，并生成可解密合并运行的启动器。

加密方式: AES-128-CBC + HMAC (Fernet, 依赖 cryptography 库)

用法:
    python exe_splitter.py <exe路径> [--chunk-size SIZE_MB] [--output-dir DIR] [--key-file FILE]

示例:
    python exe_splitter.py "Plain Craft Launcher 2.exe" --chunk-size 2
    python exe_splitter.py app.exe --key-file mykey.key   # 使用已有密钥
"""

import argparse
import hashlib
import math
import sys
import base64
from pathlib import Path

READ_BUFFER = 1024 * 1024  # 1 MB


def calculate_sha256(file_path: Path) -> str:
    """流式计算文件 SHA256，避免大文件一次性读入内存。"""
    sha = hashlib.sha256()
    with open(file_path, "rb") as f:
        while True:
            data = f.read(READ_BUFFER)
            if not data:
                break
            sha.update(data)
    return sha.hexdigest()


def generate_fernet_key() -> bytes:
    """生成一个新的 Fernet 密钥 (base64 编码的 32 字节密钥)。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("错误: 需要安装 cryptography 库")
        print("  pip install cryptography")
        sys.exit(1)
    return Fernet.generate_key()


def load_fernet_key(key_path: Path) -> bytes:
    """从文件加载 Fernet 密钥。"""
    key_data = key_path.read_bytes().strip()
    # 验证密钥格式
    try:
        base64.urlsafe_b64decode(key_data)
    except Exception:
        print("错误: 密钥文件格式不正确 (不是有效的 base64)")
        sys.exit(1)
    return key_data


def encrypt_data(data: bytes, key: bytes) -> bytes:
    """使用 Fernet 加密一段数据。"""
    from cryptography.fernet import Fernet
    f = Fernet(key)
    return f.encrypt(data)


def split_and_encrypt(exe_path: Path, chunk_size: int,
                       output_dir: Path, key: bytes) -> list:
    """
    将文件按 chunk_size 字节拆分为多个分片，每个分片用 Fernet 加密。

    命名规则: {文件名(去扩展名)}.part0000.bin, part0001.bin, ...
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    base_name = exe_path.stem

    chunk_paths = []
    index = 0

    with open(exe_path, "rb") as f:
        while True:
            data = f.read(chunk_size)
            if not data:
                break
            # 加密分片
            encrypted = encrypt_data(data, key)
            chunk_name = "{}.part{:04d}.bin".format(base_name, index)
            chunk_path = output_dir / chunk_name
            with open(chunk_path, "wb") as cf:
                cf.write(encrypted)
            chunk_paths.append(chunk_path)
            index += 1

    return chunk_paths


def generate_launcher(original_name, file_hash, chunk_count,
                       base_name, output_dir, key):
    """生成 launcher.py 启动器脚本，嵌入密钥和元数据。"""

    template = r'''#!/usr/bin/env python3
"""
launcher.py - 自动生成的启动器
解密分片 -> 合并为原始 exe -> 校验 SHA256 -> 执行。

原始文件: __ORIGINAL_NAME__
分片数量: __CHUNK_COUNT__
SHA256:   __FILE_HASH__
加密方式: Fernet (AES-128-CBC + HMAC)
"""

import base64
import hashlib
import os
import sys
import tempfile
import subprocess
from pathlib import Path

# ====== 元数据（由 exe_splitter.py 注入）======
ORIGINAL_NAME = "__ORIGINAL_NAME__"
FILE_HASH = "__FILE_HASH__"
CHUNK_COUNT = __CHUNK_COUNT__
BASE_NAME = "__BASE_NAME__"
ENCRYPTION_KEY = __ENCRYPTION_KEY__


def _pause():
    """双击运行时防止窗口闪退；命令行运行时无副作用。"""
    try:
        # 如果 stdin 不是 tty（管道/重定向），input() 会报错，忽略即可
        if sys.stdin.isatty():
            input("\n按回车键退出...")
    except (EOFError, OSError):
        pass


def _get_fernet():
    """延迟导入 Fernet，给用户友好报错。"""
    try:
        from cryptography.fernet import Fernet
    except ImportError:
        print("错误: 需要安装 cryptography 库才能解密分片")
        print("  pip install cryptography")
        sys.exit(1)
    return Fernet


def decrypt_data(data, key):
    """使用 Fernet 解密数据。如果分片损坏或被篡改，Fernet 会抛出异常。"""
    Fernet = _get_fernet()
    f = Fernet(key)
    return f.decrypt(data)


def find_chunks(directory):
    """按编号顺序查找所有分片文件，缺失则报错退出。"""
    chunks = []
    for i in range(CHUNK_COUNT):
        name = "{}.part{:04d}.bin".format(BASE_NAME, i)
        path = directory / name
        if not path.exists():
            print("错误: 找不到分片文件 {}".format(name))
            sys.exit(1)
        chunks.append(path)
    return chunks


def reassemble(chunks, output_path):
    """
    逐一解密分片后流式写入 output_path，同时计算 SHA256。
    返回合并后文件的哈希值。
    """
    sha = hashlib.sha256()
    with open(output_path, "wb") as out:
        for i, cp in enumerate(chunks):
            print("  解密分片 [{}/{}]: {}".format(
                i + 1, CHUNK_COUNT, cp.name))
            with open(cp, "rb") as f:
                encrypted_data = f.read()
            try:
                plain_data = decrypt_data(encrypted_data, ENCRYPTION_KEY)
            except Exception as e:
                print("错误: 分片 {} 解密失败: {}".format(cp.name, e))
                print("  可能原因: 密钥不匹配、分片损坏或被篡改")
                sys.exit(1)
            out.write(plain_data)
            sha.update(plain_data)
    return sha.hexdigest()


def main():
    script_dir = Path(__file__).parent

    print("正在查找分片 (共 {} 个)...".format(CHUNK_COUNT))
    chunks = find_chunks(script_dir)

    # 写入临时目录
    temp_dir = Path(tempfile.mkdtemp(prefix="exe_launcher_"))
    exe_path = temp_dir / ORIGINAL_NAME

    print("正在解密并合并到临时文件: {}".format(exe_path))
    actual_hash = reassemble(chunks, exe_path)

    size = os.path.getsize(exe_path)
    print("解密合并完成, 大小: {:.2f} MB".format(size / 1024 / 1024))

    print("正在校验完整性 (SHA256)...")
    if actual_hash != FILE_HASH:
        print("错误: 校验失败! 合并文件与原始文件不匹配。")
        print("  期望: {}".format(FILE_HASH))
        print("  实际: {}".format(actual_hash))
        sys.exit(1)
    print("校验通过")

    print("启动程序: {}".format(ORIGINAL_NAME))
    if sys.platform == "win32":
        os.startfile(str(exe_path))
    else:
        os.chmod(str(exe_path), 0o755)
        subprocess.Popen([str(exe_path)])

    print("")
    print("程序已启动。")
    print("临时文件目录: {}".format(temp_dir))
    print("如需清理, 请在程序关闭后删除上述目录。")


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        # sys.exit() 触发，窗口保持打开让用户看到错误信息
        _pause()
        raise
    except Exception as e:
        import traceback
        print("")
        print("发生未预期的错误:")
        traceback.print_exc()
        _pause()
        sys.exit(1)
    # 正常结束时也暂停，避免双击闪退
    _pause()
'''

    # 把密钥编码为 Python bytes 字面量
    key_repr = repr(key)

    code = template
    code = code.replace("__ORIGINAL_NAME__", original_name)
    code = code.replace("__FILE_HASH__", file_hash)
    code = code.replace("__CHUNK_COUNT__", str(chunk_count))
    code = code.replace("__BASE_NAME__", base_name)
    code = code.replace("__ENCRYPTION_KEY__", key_repr)

    launcher_path = output_dir / "launcher.py"
    with open(launcher_path, "w", encoding="utf-8") as f:
        f.write(code)
    return launcher_path


def generate_bat_wrapper(output_dir: Path):
    """
    生成 run.bat 批处理文件，双击即可运行 launcher.py。

    作用:
    - 用 PATH 中的 python 显式调用，绕过 .py 文件关联问题
    - 无论成功失败都会 pause，窗口不会闪退
    """
    bat_content = (
        "@echo off\r\n"
        "chcp 65001 >nul 2>&1\r\n"
        "cd /d \"%~dp0\"\r\n"
        "echo ========================================\r\n"
        "echo  EXE 加密分片启动器\r\n"
        "echo ========================================\r\n"
        "echo.\r\n"
        "\r\n"
        "python launcher.py\r\n"
        "\r\n"
        "echo.\r\n"
        "pause\r\n"
    )
    bat_path = output_dir / "run.bat"
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)
    return bat_path


def main():
    parser = argparse.ArgumentParser(
        description="将 exe 拆分为多个加密分片，并生成可解密合并运行的启动器脚本。"
    )
    parser.add_argument("exe_path", type=str, help="要拆分的 exe 文件路径")
    parser.add_argument(
        "--chunk-size",
        type=float,
        default=10.0,
        help="每个分片大小 (MB), 支持小数如 0.5, 默认 10",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="输出目录, 默认在 exe 同级目录下创建 _parts 文件夹",
    )
    parser.add_argument(
        "--key-file",
        type=str,
        default=None,
        help="使用已有的密钥文件; 不指定则自动生成新密钥并保存为 key.bin",
    )

    args = parser.parse_args()

    exe_path = Path(args.exe_path).resolve()

    # --- 输入校验 ---
    if not exe_path.exists():
        print("错误: 文件不存在: {}".format(exe_path))
        sys.exit(1)
    if not exe_path.is_file():
        print("错误: 路径不是文件: {}".format(exe_path))
        sys.exit(1)
    if args.chunk_size <= 0:
        print("错误: 分片大小必须大于 0")
        sys.exit(1)

    # --- 输出目录 ---
    if args.output_dir:
        output_dir = Path(args.output_dir).resolve()
    else:
        output_dir = exe_path.parent / "{}_parts".format(exe_path.stem)

    # --- 密钥处理 ---
    if args.key_file:
        key_path = Path(args.key_file).resolve()
        if not key_path.exists():
            print("错误: 密钥文件不存在: {}".format(key_path))
            sys.exit(1)
        key = load_fernet_key(key_path)
        print("使用已有密钥: {}".format(key_path))
    else:
        print("正在生成加密密钥...")
        key = generate_fernet_key()
        key_path = output_dir / "key.bin"
        # output_dir 在 split_and_encrypt 中创建，这里先确保存在
        output_dir.mkdir(parents=True, exist_ok=True)
        key_path.write_bytes(key)
        print("密钥已保存: {}".format(key_path))
        print("⚠ 请妥善保管密钥文件，没有密钥将无法解密分片")

    # 浮点 MB 转为整数字节数
    chunk_size_bytes = int(args.chunk_size * 1024 * 1024)
    if chunk_size_bytes <= 0:
        print("错误: 分片大小过小, 转换为字节后为 0 ({} MB < {} bytes)".format(
            args.chunk_size, 1024 * 1024))
        sys.exit(1)
    file_size = exe_path.stat().st_size

    # --- 打印信息 ---
    print("")
    print("源文件: {}".format(exe_path.name))
    print("大小: {:.2f} MB ({} bytes)".format(file_size / 1024 / 1024, file_size))

    print("正在计算 SHA256...")
    file_hash = calculate_sha256(exe_path)
    print("SHA256: {}".format(file_hash))

    expected_chunks = math.ceil(file_size / chunk_size_bytes)
    print("分片大小: {} MB".format(args.chunk_size))
    print("预计分片数: {}".format(expected_chunks))
    print("输出目录: {}".format(output_dir))
    print("")

    # --- 拆分并加密 ---
    print("正在拆分并加密...")
    chunk_paths = split_and_encrypt(exe_path, chunk_size_bytes,
                                     output_dir, key)

    print("加密拆分完成, 共 {} 个分片:".format(len(chunk_paths)))
    for i, cp in enumerate(chunk_paths):
        sz = cp.stat().st_size
        print("  [{}/{}] {}  ({:.2f} MB)".format(
            i + 1, len(chunk_paths), cp.name, sz / 1024 / 1024))

    # --- 生成启动器 ---
    print("")
    print("正在生成启动器...")
    launcher_path = generate_launcher(
        original_name=exe_path.name,
        file_hash=file_hash,
        chunk_count=len(chunk_paths),
        base_name=exe_path.stem,
        output_dir=output_dir,
        key=key,
    )
    print("启动器: {}".format(launcher_path))

    # --- 生成批处理包装 ---
    print("正在生成批处理启动器...")
    bat_path = generate_bat_wrapper(output_dir)
    print("批处理: {}".format(bat_path))

    print("")
    print("========== 使用说明 ==========")
    print("1. 将 {} 文件夹整体复制到目标位置".format(output_dir.name))
    print("2. 目标机器需要安装 cryptography: pip install cryptography")
    print("3. 双击 run.bat 即可启动 (推荐)")
    print("   或命令行运行: python launcher.py")
    print("4. 启动器自动解密分片 -> 合并 -> 校验 SHA256 -> 执行程序")
    print("5. 密钥已嵌入 launcher.py，key.bin 仅作备份")
    print("==============================")


if __name__ == "__main__":
    main()
