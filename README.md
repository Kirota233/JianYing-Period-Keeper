# JianYing-Period-Keeper (剪映句读控制器)

> **Swiss International Typographic Style · Minimalist Edition**  
> 专门解决剪映电脑版「文稿识别 / 文稿匹配」自动删除中英文句号与逗号问题的单文件辅助工具。

---

## ✦ 特性亮点

* **精准标点控制**：仅针对剪映默认吞掉的**中英文句号（`。` / `.`）**与**中英文逗号（`，` / `,`）**进行拦截豁免；感叹号、问号、省略号等情感标点维持原生保留。
* **单开关极简交互**：极简拨动开关（Toggle Switch），拨至 `[ ON ]` 立即开启保留，拨至 `[ OFF ]` 一键还原官方默认过滤。
* **动态内存热注入**：零篡改磁盘文件，100% 保持字节跳动官方 Authenticode 数字签名完好，彻底杜绝应用闪退与打不开问题。
* **剪贴板全息保护**：双轨守护，确保复制文稿粘贴到剪映文稿匹配框时标点完整留存。
* **在线签名与版本管理**：内置 `auth.json` 云端状态感知、版本对比与一键热更新机制。

---

## ✦ 使用步骤

1. 打开电脑版剪映。
2. 打开本工具，将拨动开关拨至 **[ON]**。
3. 在剪映中使用文稿匹配或文稿识别字幕。
4. 如需恢复官方默认效果，将开关拨回 **[OFF]**。

---

## ✦ 快速下载与运行

前往 [Releases 页面](https://github.com/Kirota233/JianYing-Period-Keeper/releases) 下载最新版本的 `JianyingPeriodKeeper.exe`，无需安装 Python 环境，双击即开即用。

---

## ✦ 本地构建

```bash
# 1. 克隆仓库
git clone https://github.com/Kirota233/JianYing-Period-Keeper.git
cd JianYing-Period-Keeper

# 2. 安装依赖
pip install -r requirements.txt
pip install pyinstaller

# 3. 打包单文件 EXE
pyinstaller --onefile --noconsole --name "JianyingPeriodKeeper" JianyingPeriodKeeper.py
```

---

## ✦ 协议与声明

本项目仅供个人音视频创作学习与交流使用。
