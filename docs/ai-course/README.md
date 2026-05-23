# AI课程材料 - 四年级人工智能入门课

## 📁 文件说明

| 文件 | 说明 |
|------|------|
| `presentation.md` | PPT演示文稿（Marp格式，约51页） |
| `lecture-script.md` | 配套详细讲稿 |

## 🎯 课程目标

面向小学四年级学生，通过大量比喻和实例，讲解人工智能的基本概念、工作原理、应用场景和未来展望。课程结合 RoundTable 项目作为真实案例。

## 📖 课程结构

1. **AI是什么** - 基本概念、与普通电脑的区别
2. **AI怎么学习** - 数据、训练、神经网络、深度学习
3. **AI的能力** - 自然语言处理、计算机视觉、语音识别/合成
4. **AI的应用** - 医疗、交通、教育、游戏、艺术
5. **AI的未来** - 通用人工智能、发展展望

## 🛠️ 如何导出为 PPTX

### 方法一：使用 Marp CLI

```bash
# 安装 Marp CLI
npm install -g @marp-team/marp-cli

# 导出为 PPTX
marp presentation.md --pptx --allow-local-files

# 导出为 PDF
marp presentation.md --pdf --allow-local-files
```

### 方法二：使用 VS Code

1. 安装 VS Code 插件 "Marp for VS Code"
2. 打开 `presentation.md`
3. 点击右上角导出按钮，选择 PPTX 格式

### 方法三：手动制作

使用 `presentation.md` 中的内容作为每一页的文案，在 PowerPoint/Keynote/Google Slides 中手动排版。建议：
- 将 ASCII 图表替换为实际插图
- 使用卡通风格的图标和配图
- 每页配上相关的可爱插画

## 💡 教学建议

- 总时长建议 45-60 分钟
- 每5-6页安排一次简短互动
- 鼓励学生随时提问
- 最后留出10分钟进行小测验和自由讨论
- 可以现场演示 RoundTable 的讨论功能
