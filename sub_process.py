import sys
import os
import re
import chardet
from opencc import OpenCC
import datetime
import yaml
# import json
# import configparser

# 默认ASS文件头
ass_header = """[Script Info]
Title: Default Aegisub file
ScriptType: v4.00+
WrapStyle: 0
PlayResX: 1280
PlayResY: 960
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Microsoft YaHei,60,&H0000FFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
# 常见的字幕类型
sub_filetype = ['.srt', '.ass', '.ssa']

# 时间格式正则表达式（兼容ASS和SRT）
SUB_TIME_RE = re.compile(r'\d{1,2}:\d{2}:\d{2}[,.]\d{2,3}')

# ----------  针对SRT格式的正则 ----------
SRT_SPLIT_RE = re.compile(r'\r?\n\s*\n',flags=re.UNICODE|re.MULTILINE)          # 段落分隔
SRT_BLOCK_RE = re.compile(
    r'^\s*(\d+)\s*\n'                             # 序号
    r'(\d{2}:\d{2}:\d{2}[,.]\d{3}\s*-->\s*\d{2}:\d{2}:\d{2}[,.]\d{3})\s*\n'  # 时间轴
    r'([\s\S]*)$',
    flags=re.UNICODE|re.MULTILINE     # 字幕文本
)

# ----------  针对ASS格式的正则 ----------
ASS_HEADER_RE = re.compile(r'^.*?(?=Dialogue)', re.DOTALL)
ASS_DIALOGUE_RE = re.compile(r'^(Dialogue:\s*\d+,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,[^,]*,)(.*)$', flags=re.UNICODE|re.MULTILINE)

# ###########需要清理的内容#######################
# 去掉开头的标点符号和空白符
BLANK_HEAD_RE = re.compile(r'^[^\w(（\'"‘“]+', flags=re.UNICODE|re.MULTILINE)
# text = re.sub(r'^[\W]+', '', text, flags=re.UNICODE)

# 去掉所有尾部标点（不包括问号和叹号）
BLANK_TAIL_RE = re.compile(r'[,.:;，。：；、\s]+$', flags=re.UNICODE|re.MULTILINE)

# 将文本中重复了2次及以上的多字字符串替换为1次
REPEAT_CONTENT_RE = re.compile(r'(..+?)(\1){1,}', flags=re.UNICODE|re.MULTILINE)# 匹配任何重复至少2次的双字及以上的子字符串

# 如果一行中有重叠的2个及以上语气词，则只保留1个
REPEAT_CHAR_RE = re.compile(r'([ ,.，。！!?？：；;嗯呵哒喽呗嘛哟哇呃啊哦啦唉欸诶喔呀呐哼哈喂]){2,}',flags=re.UNICODE)

# 如果一行完全由语气词（呃 / 诶 / 啊…，但‘嗯’则保留）或标点组成，则替换为空
BLANK_RE = re.compile(r'^[ ,.，。！!?？：；;—\-\–…\"\'「」『』()（）嗯呵哒喽呗嘛哟哇呃啊哦啦唉欸诶喔呀呐哼哈嘿喂]*$', flags=re.UNICODE|re.MULTILINE)
###############################################################


def change_to_exe_dir():
    # 获取可执行文件所在的目录路径
    if getattr(sys, 'frozen', False):
        # 如果是打包后的exe文件
        exe_dir = os.path.dirname(sys.executable)
    else:
        # 如果是源代码运行
        exe_dir = os.path.dirname(os.path.abspath(__file__))

    # 修改当前工作目录
    os.chdir(exe_dir)
    return exe_dir

class Subtitle_process:
    def __init__(self, sub_path, is_srt2ass=True, config_file='config_ini'):
        self.sub_path = sub_path
        self.is_srt2ass = is_srt2ass
        self.ass_style = ass_header
        self.config_file = config_file

        self.replace_words = {}
        self.max_duration = 7

        self.sub_files = []
        # 当前正在处理的字幕文件：
        self.current_file = ''
        self.current_content = ''
        self.current_root = ''
        self.current_ext = ''

    # 判断所给的文件路径是否为文件或文件夹，如果是字幕文件，则添加到列表中；否则遍历该文件夹，添加所有字幕文件到列表中
    def find_sub_files(self):
        if os.path.isfile(self.sub_path):
            _, ext = os.path.splitext(self.sub_path)
            if ext.lower() in sub_filetype:
                self.sub_files.append(self.sub_path)

        elif os.path.isdir(self.sub_path):
            for root, dirs, files in os.walk(self.sub_path):
                try:
                    for file in files:
                        _, ext = os.path.splitext(file)
                        if ext.lower() in sub_filetype:
                            self.sub_files.append(os.path.join(root, file))

                except PermissionError:
                    print("权限错误，无法访问该文件或文件夹！！")
                    continue  # 跳过无权限目录

        # 如果列表为空，则返回错误
        if not self.sub_files:
            print(f"错误：'{self.sub_path}' 不存在或该文件夹下没有字幕文件。")
            sys.exit(1)

    # 读取配置文件
    def read_yaml_config(self):
        # 是否存在config.yml文件：
        if os.path.exists(self.config_file):  # 检查文件是否存在
            print(f'找到并将使用 配置文件：{self.config_file}')
            try:
                yaml_config = yaml.load(open(self.config_file, 'r', encoding='utf-8'), Loader=yaml.FullLoader)

                # 读取 max_duration 的值
                self.max_duration = yaml_config.get("max_duration", 7)
                print(f"max_duration: {self.max_duration}")

                # 读取配置文件中的替换字典，将整个section转为字典
                self.replace_words = yaml_config.get("replacements", {})
                if self.replace_words:
                    print(f"找到并将使用 替换单词")
                # for key, value in self.replace_words.items():
                #     print(f"替换单词：{key} -> {value}")

                # 处理ass_file文件：
                ass_file = yaml_config.get('ass_file', '')
                if ass_file and os.path.exists(ass_file):
                    with open(ass_file, 'r', encoding='utf-8') as f:
                        # print(f'找到并使用了配置的ASS文件：{ass_file}\n')
                        ass_content = f.read().strip()
                        match = ASS_HEADER_RE.search(ass_content)
                        if match:
                            self.ass_style = match.group(0)
                            print(f"找到并将使用 配置的ASS文件\n")
                        else:
                            print(f"警告：配置的ASS文件未发现有效的ass文件头：{ass_file}\n")
                            self.ass_style = ass_header
                else:
                    print(f'警告：没有或未找到模板ASS文件：{ass_file}\n')

            except yaml.YAMLError:
                print(f"错误：'{self.config_file}' 不是一个有效的YAML文件。\n")
                return
            except Exception as e:
                print(f"错误：'{self.config_file}' 读取失败：{e}\n")
                return

        else:
            print(f"警告：没有或未找到配置文件：{self.config_file}\n")


    # 检测字幕的文件编码，并将编码以字符串的形式返回
    def detect_encoding(self, file_path) -> str:
        """检测文件编码"""
        with open(file_path, 'rb') as f:
            raw_data = f.read()
            result = chardet.detect(raw_data)
            encoding = result['encoding'] if result['confidence'] > 0.7 else 'utf-8'
            # 处理常见编码别名问题
            encoding = 'gb18030' if encoding.lower() in ['gbk', 'gb2312'] else encoding
            return encoding

    # 读取当前字幕文件，并将内容保存在属性中
    def read_sub_file(self):
        if not self.current_file:
            return
        self.current_root, self.current_ext = os.path.splitext(self.current_file)
        # if self.current_ext.lower() not in sub_filetype:
        #     print(f"错误：'{self.current_file}' 不是一个有效的字幕文件。")
        #     return
        encoding = self.detect_encoding(self.current_file)
        try:
            with open(self.current_file, 'r', encoding=encoding) as f:
                self.current_content = f.read()
        except UnicodeDecodeError:
            # 如果检测失败，尝试常见编码
            for enc in ['utf-8', 'gbk', 'big5', 'latin1']:
                try:
                    with open(self.current_file, 'r', encoding=enc) as f:
                        self.current_content = f.read()
                        self.current_encoding = enc
                    break
                except UnicodeDecodeError:
                    self.current_content = ''
                    continue

    ################################################

    # 替换和繁体转简体
    def tw2cn(self):
        # if not self.current_file:
        #     return
        # encoding = self.detect_encoding(self.current_file)
        # with open(self.current_file, 'r', encoding=encoding) as f:
        #     content = f.read()

        # 繁体转简体
        cc = OpenCC('t2s')  # 繁体转简体
        content = cc.convert(self.current_content)

        # 写入文件（UTF-8编码）
        if content:
            self.current_content = content
            self.current_encoding = 'utf-8-sig'
            with open(self.current_file, 'w', encoding='utf-8-sig') as f:  # utf-8-sig添加BOM确保兼容性
                f.write(content)

    # 清理字幕文件
    def clean_line(self, text: str) -> str:
        """
        1. 去掉首尾【一般】标点
        2. 同时清除收尾标点，结尾的问号需要保留
        """
        text = text.strip()
        if not text:
            return ''

        # 替换和删除特定词语（支持正则表达式)
        if self.replace_words:
            for old_wd, new_wd in self.replace_words.items():
                try:
                    text = re.sub(old_wd, new_wd, text, flags=re.UNICODE)
                except re.error as e:
                    print(f"'{old_wd}'正则表达式语法错误: {e}")
                    continue
                except Exception as e:
                    continue

        # 将文本中重复了2次及以上的多字字符串替换为1次
        text = REPEAT_CONTENT_RE.sub(r'\1', text)

        # 如果一行中有重叠的2个及以上语气词，则只保留1个
        text = REPEAT_CHAR_RE.sub(r'\1', text)

        # 去掉开头的标点符号和空白符
        text = BLANK_HEAD_RE.sub(r'', text)

        # 去掉所有尾部标点（不包括问号和叹号）
        text = BLANK_TAIL_RE.sub(r'', text)

        # 如果一行完全由语气词（呃 / 诶 / 啊…，但‘嗯’则保留）或标点组成，则替换为空
        text = BLANK_RE.sub(r'', text)

        return text.strip()


    # 处理srt文件
    def process_srt(self):
        if not self.current_content:
            return

        blocks = SRT_SPLIT_RE.split(self.current_content)
        cleaned_blocks = []
        counter = 1
        for blk in blocks:
            m = SRT_BLOCK_RE.match(blk.strip())
            if not m:  # 不是合法 block
                continue
            seq, timing, text = m.groups()
            # #########################################
            # 开始处理时间，格式类似于 00:00:39,560 --> 00:00:43,830
            # 提取开始和结束时间
            times = SUB_TIME_RE.findall(timing)
            if len(times) < 2:
                continue
            #
            start_time_str, end_time_str = [t.replace(',', '.') for t in times[:2]]
            # 将时间字符串转换为时间对象
            try:
                start_time = datetime.datetime.strptime(start_time_str, '%H:%M:%S.%f')
                end_time = datetime.datetime.strptime(end_time_str, '%H:%M:%S.%f')
            except ValueError:
                continue

            ############### 处理字幕的持续时间 ###################################
            # 计算持续时间（秒）
            duration = (end_time - start_time).total_seconds()

            # 如果持续时间超过max_duration秒，则调整为max_duration秒
            if duration > self.max_duration:
                end_time = start_time + datetime.timedelta(seconds=self.max_duration)
                end_time_str = end_time.strftime('%H:%M:%S,%f')[:-3]  # 保留毫秒部分3位

            if not self.is_srt2ass:     # 需要保留成SRT格式
                # 格式化时间字符串（SRT格式）
                start_time_str = start_time.strftime('%H:%M:%S,%f')[:-3]  # 保留毫秒部分3位
                end_time_str = end_time.strftime('%H:%M:%S,%f')[:-3]  # 保留毫秒部分3位
                timing = f'{start_time_str} --> {end_time_str}'
            else:       # 需要保留成ASS格式
                # 格式化时间字符串（ASS格式）
                start_time_str = start_time.strftime('%H:%M:%S.%f')[:-4]  # 保留毫秒部分2位
                end_time_str = end_time.strftime('%H:%M:%S.%f')[:-4]  # 保留毫秒部分2位

                # 处理strftime 的格式占位符会把小时 固定 为两位（%H）的问题：
                start_time_str = start_time_str[1:] if start_time_str.startswith('0') else start_time_str
                end_time_str = end_time_str[1:] if end_time_str.startswith('0') else end_time_str
            ############### 处理字幕的持续时间 结束 ###################################

            ##################################################
            # 开始清理字幕内容
            lines = text.splitlines()
            new_lines = [self.clean_line(l) for l in lines]
            new_lines = [l for l in new_lines if l]  # 删掉清洗后变空白的
            if new_lines:
                if self.is_srt2ass:
                    text = r'\N'.join(new_lines)  # ASS中使用\N表示换行
                    ass_line = f"Dialogue: 0,{start_time_str},{end_time_str},Default,,0,0,0,,{text}"
                    cleaned_blocks.append(ass_line)
                else:
                    cleaned_blocks.append(f'{counter}\n{timing}\n' + '\n'.join(new_lines))
                    counter += 1
        # 保存结果：
        if self.is_srt2ass:
            content = self.ass_style.strip() + '\n' + '\n'.join(cleaned_blocks) + '\n'
            result_path = self.current_file.replace('.srt', '.ass')
            # 9. 写入文件（UTF-8-BOM编码）
            with open(result_path, 'w', encoding='utf-8-sig') as f:  # utf-8-sig添加BOM确保兼容性
                f.write(content)
            print(f'{self.current_file} -> {result_path}')

        else:
            content = '\n\n'.join(cleaned_blocks) + '\n'
            # 9. 写入文件（UTF-8-BOM 编码）
            with open(self.current_file, 'w', encoding='utf-8-sig') as f:  # utf-8-sig添加BOM确保兼容性
                f.write(content)
            print(f'{self.current_file} -> {self.current_file}')


    # 处理 ass 格式文件：
    # ----------  ASS/SSA ----------
    def process_ass(self):
        if not self.current_content:
            return

        # 1. 先按段落保存 Script Info 等开头的元数据
        ass_header_m = ASS_HEADER_RE.search(self.current_content)
        if not ass_header_m:
            print('Error!! 不是有效的 ASS/SSA 文件！')
            return
        # # 保留原来的 ass_header
        # ass_header_origin = ass_header_m.group(0)

        # 获取字幕的正文部分：
        dialogue_part = self.current_content[ass_header_m.end():].strip()
        if not dialogue_part:
            print('Error!! ASS/SSA 文件中未包含有效字幕！')
            return

        new_dialogues = []
        for dl_line in dialogue_part.splitlines(True):
            m = ASS_DIALOGUE_RE.match(dl_line)
            if not m:
                # 格式行、样式行等等直接保留
                new_dialogues.append(dl_line)
                continue
            prefix, text = m.groups()

            # #########################################
            # 开始处理时间，格式类似于 00:00:39,560 --> 00:00:43,830
            # 提取开始和结束时间
            times = SUB_TIME_RE.findall(prefix)
            if len(times) < 2:
                continue
            start_time_str, end_time_str = [t.replace(',', '.') for t in times[:2]]
            # 将时间字符串转换为时间对象
            try:
                start_time = datetime.datetime.strptime(start_time_str, '%H:%M:%S.%f')
                end_time = datetime.datetime.strptime(end_time_str, '%H:%M:%S.%f')
            except ValueError:
                continue

            # 将时间字符串转换为时间对象
            try:
                start_time = datetime.datetime.strptime(start_time_str, '%H:%M:%S.%f')
                end_time = datetime.datetime.strptime(end_time_str, '%H:%M:%S.%f')
            except ValueError:
                continue

            # 计算持续时间（秒）
            duration = (end_time - start_time).total_seconds()

            # 如果持续时间超过max_duration秒，则调整为max_duration秒
            if duration > self.max_duration:
                end_time = start_time + datetime.timedelta(seconds=self.max_duration)
                end_time_str = end_time.strftime('%H:%M:%S.%f')[:-4]  # 保留毫秒部分2位

            # 格式化时间字符串（ASS格式）
            start_time_str = start_time.strftime('%H:%M:%S.%f')[:-4]  # 保留毫秒部分2位
            end_time_str = end_time.strftime('%H:%M:%S.%f')[:-4]  # 保留毫秒部分2位

            # 处理strftime 的格式占位符会把小时 固定 为两位（%H）的问题：
            start_time_str = start_time_str[1:] if start_time_str.startswith('0') else start_time_str
            end_time_str = end_time_str[1:] if end_time_str.startswith('0') else end_time_str

            prefix = f"Dialogue: 0,{start_time_str},{end_time_str},Default,,0,0,0,,"
            ##################################################

            # ASS 文本常带 \N 手动换行
            lines = text.replace(r'\N', '\n').splitlines()
            cleaned = [self.clean_line(l) for l in lines]
            cleaned_txt = r'\N'.join(cleaned)
            if cleaned_txt:
                new_dialogues.append(prefix + cleaned_txt)
            # 空白则直接丢弃该行

        content = self.ass_style.strip() + '\n' + '\n'.join(new_dialogues)
        # 增加对于 .ssa 文件的处理
        result_path = self.current_file.replace('.ssa', '.ass')
        # 9. 写入文件（UTF-8-BOM编码）
        with open(result_path, 'w', encoding='utf-8-sig') as f:  # utf-8-sig添加BOM确保兼容性
            f.write(content)
        print(f'{self.current_file} -> {result_path}')


    def process_all(self):
        self.read_yaml_config()
        self.find_sub_files()
        i = 0
        count = len(self.sub_files)
        for sub_file in self.sub_files:
            self.current_file = sub_file
            self.read_sub_file()
            self.tw2cn()
            if self.current_ext == '.srt':
                self.process_srt()
            else:
                self.process_ass()
            i = i + 1
            print(f"已处理 {i}/{count} 个文件。\n")
        print(f"全部 {count} 字幕文件已处理完成！")

def main():
    new_path = change_to_exe_dir()
    print(f"当前工作目录已修改为: \t{new_path}")

    # 检查参数数量
    if len(sys.argv) < 2:
        print("错误：请至少提供文件或文件夹路径作为参数。")
        print("用法: 工具.exe", sys.argv[0], "<文件路径>", "[is_srt2ass]")
        sys.exit(1)
    elif len(sys.argv) == 2:    # 只有一个参数时，单纯执行字幕繁->简、替换等处理功能
        is_srt2ass = False
    else:   # 多于一个参数时，执行字幕srt->ass的功能
        is_srt2ass = True

    # 获取文件路径
    sub_path = sys.argv[1]

    # # 手动测试时：
    # sub_path = r"test\test.srt"
    # is_srt2ass = True

    # 实例化类并执行其中的总流程：
    sub_proces = Subtitle_process(sub_path, is_srt2ass, config_file='config.yml')
    sub_proces.process_all()


if __name__ == "__main__":
    main()
