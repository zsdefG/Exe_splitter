# exe_splitter 更新公告

## v2.0 — Process Hollowing 内存执行架构（2026-07-31）

### 重大变更：从"落盘运行"到"内存执行"

旧版启动器的工作方式是：解密分片 → 写入临时文件 → 通过 subprocess 运行临时文件 → 退出后删除。这有两个问题：

1. 解密后的完整 EXE 会在磁盘上短暂存在，可能被其他进程读取或被安全软件拦截
2. 分片加密形同虚设——解密产物完整暴露在磁盘上

新版完全重写了执行层，核心思路是**解密后的 PE 字节流全程不接触文件系统**。

---

### 新增：Process Hollowing（进程镂空）

```
旧流程:  解密 → [落盘: demo.exe] → 运行 → [清理]
新流程:  解密 → [内存] → 注入到挂起的 cmd.exe → 从内存启动
```

- 创建挂起（CREATE_SUSPENDED）的 cmd.exe 宿主进程
- `NtUnmapViewOfSection` 卸载 cmd.exe 原镜像
- `VirtualAllocEx` 从目标进程分配内存
- `WriteProcessMemory` 写入 PE 头 + 各节区
- 修正 PEB、设置入口地址 `Eax/Rcx`
- `ResumeThread` 恢复执行，Windows Loader 自动完成 DLL 加载、导入表解析、重定位

整个过程解密后的 PE 字节**全程在内存中流转**，不产生任何磁盘文件。

---

### 修复：64-bit PE 支持

旧版代码跳过了 `NtUnmapViewOfSection`（注释写着"避免 PEB 损坏"），导致 64-bit EXE 启动后瞬间崩溃——cmd.exe 原镜像残留，Windows Loader 恢复线程时发现 PEB 状态混乱。

新版严格遵循标准 RunPE 流程，**ResumeThread 前必定先 Unmap**。geek.exe（32-bit）和 demo.exe（64-bit）均已验证通过。

---

### 修复：WOW64 PEB 读取

32-bit EXE 在 64-bit Windows 上通过 WOW64 层运行时，`NtQueryInformationProcess(ProcessBasicInformation)` 返回的是 64-bit PEB 地址。之前代码直接读 `PEB+8` 获取 ImageBase，拿到的实际是 PEB 高 32 位。

改为 `NtQueryInformationProcess(ProcessWow64Information=26)` 直接获取 32-bit 的 PEB 指针，ImageBase 读取正确。

---

### 新增：智能回退 — 自动落盘降级

并非所有 EXE 都能从内存执行。以下场景会自动回退到临时文件模式：

| 失败场景 | 检测方式 | 触发时机 |
|---------|---------|---------|
| ImageBase 冲突且无 .reloc 段 | PE 解析时检测 | 注入前 |
| 导入表含磁盘依赖函数 | 导入表静态扫描 | 注入前 |
| 进程启动后异常崩溃 | 存活检测轮询 | 注入后 10s 内 |

回退流程：`tempfile.mkdtemp()` → 写入临时文件 → `subprocess.Popen` → 程序退出后 `atexit` 自动清理临时目录。

---

### 新增：PE 存活检测

Process Hollowing 注入后，某些打包类 EXE（如 PyInstaller）不会立即崩溃——bootloader 需要数秒初始化 Python 运行时，之后调用 `GetModuleFileName` 发现路径不对才退出。

旧版代码恢复线程后直接等待，把启动失败当成正常退出。新版在 `ResumeThread` 后执行 10 秒轮询存活检测：

```
ResumeThread 后每 200ms 检查一次进程状态（最长 10 秒）
  ├─ 进程退出 → raise RuntimeError → 触发 temp-file 回退
  └─ 进程存活 → 认为注入成功 → WaitForSingleObject(INFINITE) 正常等待
```

---

### 新增：导入表静态扫描

作为存活检测的**前置防线**，注入前扫描 PE 导入表，检测以下"需要读取自身磁盘文件"的函数：

- `GetModuleFileNameA/W` — 打包器/壳读取自身路径
- `FindResourceA/W/ExA/ExW` — 读取嵌入资源
- `LoadResource` / `SizeofResource` / `LockResource` — 资源加载

命中任一函数 → 直接跳过 Hollowing，立即走 temp-file 回退。避免浪费 10 秒等待。

> 注：部分程序通过 `GetProcAddress` 动态加载这些函数，静态扫描无法捕获。此时由存活检测兜底。

---

### 新增：bat 包装器

输出目录自动生成 `run.bat`，用户双击即可运行，无需手动打开终端输入命令。

---

### 测试矩阵

| EXE | 类型 | 空路径 | 结果 |
|-----|------|--------|------|
| geek.exe | 32-bit, 原生 | Hollowing | 内存执行成功 |
| geek.exe (冲突) | 32-bit, ImageBase 冲突 | Hollowing 失败 → 落盘 | 落盘回退成功 |
| demo.exe | 64-bit, PyInstaller 打包 | Hollowing → 6.4s 崩溃检测 → 落盘 | 回退成功，输出 "Hello,world!" |

---

### 安全特性

- 解密后的 PE 字节全程不接触文件系统（Hollowing 成功时）
- 临时文件模式下，`atexit` 确保即使程序异常退出也会清理
- 密钥嵌入 launcher.py，`key.bin` 仅作备份
- SHA256 完整性校验，防止分片损坏或被篡改

---

### 已知限制

- 目标机器需安装 `cryptography` 库（`pip install cryptography`）
- 需要 `.reloc` 段的 PE 在 ImageBase 冲突时才能 Hollowing 成功（否则走落盘降级）
- 部分深度加壳/反调试 PE 可能被安全软件拦截
