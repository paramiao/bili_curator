#!/usr/bin/env python3
"""
B站视频目录管理脚本 - 修复版本
专门修复文件名和nfo生成问题
"""

import os
import sys
import json
import re
import argparse
from pathlib import Path
import xml.etree.ElementTree as ET
from datetime import datetime
import logging

class BilibiliDirectoryManagerFixed:
    def __init__(self, directory):
        self.directory = Path(directory)
        if not self.directory.exists():
            raise ValueError(f"目录不存在: {directory}")
        
        self.setup_logging()
        
    def setup_logging(self):
        """设置日志"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(self.directory / 'directory_manager.log', encoding='utf-8'),
                logging.StreamHandler(sys.stdout)
            ]
        )
        self.logger = logging.getLogger(__name__)
    
    def process_directory(self):
        """处理整个目录"""
        self.logger.info(f"开始处理目录: {self.directory}")
        
        # 1. 扫描并分析文件
        video_groups = self.scan_video_groups()
        self.logger.info(f"找到 {len(video_groups)} 个视频组")
        
        if not video_groups:
            self.logger.warning("没有找到有效的视频文件")
            return
        
        # 2. 重命名文件
        self.rename_all_files(video_groups)
        
        # 3. 生成丰富的nfo文件
        self.generate_rich_nfo_files(video_groups)
        
        # 4. 生成视频ID列表
        self.generate_video_id_list(video_groups)
        
        # 5. 清理重复和临时文件
        self.cleanup_files()
        
        self.logger.info("目录处理完成")
    
    def scan_video_groups(self):
        """扫描并分组视频文件"""
        self.logger.info("扫描视频文件...")
        
        # 查找所有info.json文件
        info_files = list(self.directory.glob('*.info.json'))
        self.logger.info(f"找到 {len(info_files)} 个info.json文件")
        
        video_groups = []
        
        for info_file in info_files:
            self.logger.info(f"\n处理info文件: {info_file.name}")
            
            try:
                # 读取info.json
                with open(info_file, 'r', encoding='utf-8') as f:
                    video_info = json.load(f)
                
                # 获取基础信息 - 关键修复点
                video_id = video_info.get('id', '')
                title = video_info.get('title', '')
                
                self.logger.info(f"  视频ID: {video_id}")
                self.logger.info(f"  原始标题: '{title}'")
                
                if not video_id:
                    self.logger.warning(f"  跳过：没有视频ID")
                    continue
                
                if not title:
                    # 尝试其他标题字段
                    title = video_info.get('fulltitle', '') or video_info.get('display_id', '')
                    self.logger.warning(f"  使用备用标题: '{title}'")
                
                if not title:
                    self.logger.warning(f"  跳过：无法获取标题")
                    continue
                
                # 获取原始前缀（去掉.info.json）
                original_prefix = info_file.stem
                self.logger.info(f"  原始前缀: {original_prefix}")
                
                # 查找相关文件
                related_files = self.find_related_files(original_prefix)
                self.logger.info(f"  找到相关文件: {len(sum(related_files.values(), []))}个")
                
                video_group = {
                    'video_id': video_id,
                    'title': title,
                    'original_prefix': original_prefix,
                    'info_file': info_file,
                    'video_info': video_info,
                    'related_files': related_files
                }
                
                video_groups.append(video_group)
                self.logger.info(f"  ✓ 添加到处理列表")
                
            except Exception as e:
                self.logger.error(f"  ❌ 处理info文件失败: {e}")
                import traceback
                self.logger.error(f"  详细错误: {traceback.format_exc()}")
        
        return video_groups
    
    def find_related_files(self, prefix):
        """查找相关文件"""
        related_files = {
            'video': [],
            'thumbnail': [],
            'info': [],
            'nfo': [],
            'others': []
        }
        
        # 查找所有相关文件
        pattern_files = list(self.directory.glob(f'{prefix}.*'))
        
        for file in pattern_files:
            if file.suffix == '.json' and 'info' in file.name:
                related_files['info'].append(file)
            elif file.suffix.lower() in ['.mp4', '.flv', '.mkv', '.webm']:
                related_files['video'].append(file)
            elif file.suffix.lower() in ['.jpg', '.png', '.webp']:
                related_files['thumbnail'].append(file)
            elif file.suffix == '.nfo':
                related_files['nfo'].append(file)
            else:
                related_files['others'].append(file)
        
        return related_files
    
    def rename_all_files(self, video_groups):
        """重命名所有文件"""
        self.logger.info("\\n" + "="*50)
        self.logger.info("开始重命名文件...")
        self.logger.info("="*50)
        
        for i, group in enumerate(video_groups, 1):
            try:
                title = group['title']
                safe_title = self.sanitize_filename(title)
                
                self.logger.info(f"\\n[{i}/{len(video_groups)}] 重命名组: {group['video_id']}")
                self.logger.info(f"  原始标题: '{title}'")
                self.logger.info(f"  安全标题: '{safe_title}'")
                
                if not safe_title or safe_title == 'untitled':
                    self.logger.warning(f"  ⚠️ 标题清理后为空，跳过重命名")
                    continue
                
                # 重命名各类文件
                success = self.rename_file_group(group, safe_title)
                
                if success:
                    self.logger.info(f"  ✅ 重命名成功")
                    # 更新组信息中的文件路径
                    group['safe_title'] = safe_title
                else:
                    self.logger.warning(f"  ⚠️ 重命名部分失败")
                
            except Exception as e:
                self.logger.error(f"  ❌ 重命名失败: {e}")
                import traceback
                self.logger.error(f"  详细错误: {traceback.format_exc()}")
    
    def rename_file_group(self, group, new_name):
        """重命名一组文件"""
        related_files = group['related_files']
        success_count = 0
        total_files = 0
        
        # 重命名视频文件
        for video_file in related_files['video']:
            total_files += 1
            new_file = self.directory / f"{new_name}{video_file.suffix}"
            if self.rename_file_safely(video_file, new_file):
                success_count += 1
        
        # 重命名缩略图
        for thumb_file in related_files['thumbnail']:
            total_files += 1
            new_file = self.directory / f"{new_name}{thumb_file.suffix}"
            if self.rename_file_safely(thumb_file, new_file):
                success_count += 1
        
        # 重命名info.json
        for info_file in related_files['info']:
            total_files += 1
            new_file = self.directory / f"{new_name}.info.json"
            if self.rename_file_safely(info_file, new_file):
                success_count += 1
                # 更新组信息
                group['info_file'] = new_file
        
        # 删除旧的nfo文件（稍后会生成新的）
        for nfo_file in related_files['nfo']:
            try:
                nfo_file.unlink()
                self.logger.info(f"    删除旧nfo: {nfo_file.name}")
            except Exception as e:
                self.logger.warning(f"    删除旧nfo失败: {e}")
        
        # 重命名其他文件
        for other_file in related_files['others']:
            total_files += 1
            new_file = self.directory / f"{new_name}{other_file.suffix}"
            if self.rename_file_safely(other_file, new_file):
                success_count += 1
        
        self.logger.info(f"    重命名结果: {success_count}/{total_files} 个文件成功")
        return success_count > 0
    
    def rename_file_safely(self, old_file, new_file):
        """安全重命名文件"""
        if old_file.name == new_file.name:
            self.logger.info(f"    跳过: {old_file.name} (名称已正确)")
            return True
        
        if new_file.exists():
            self.logger.warning(f"    跳过: {new_file.name} (目标文件已存在)")
            return False
        
        try:
            old_file.rename(new_file)
            self.logger.info(f"    重命名: {old_file.name} -> {new_file.name}")
            return True
        except Exception as e:
            self.logger.error(f"    重命名失败: {old_file.name} -> {new_file.name}: {e}")
            return False
    
    def generate_rich_nfo_files(self, video_groups):
        """生成丰富的nfo文件"""
        self.logger.info("\\n" + "="*50)
        self.logger.info("生成丰富的nfo文件...")
        self.logger.info("="*50)
        
        for i, group in enumerate(video_groups, 1):
            try:
                title = group['title']
                safe_title = group.get('safe_title') or self.sanitize_filename(title)
                video_info = group['video_info']
                
                self.logger.info(f"\\n[{i}/{len(video_groups)}] 生成nfo: {safe_title}")
                
                nfo_path = self.directory / f"{safe_title}.nfo"
                
                # 删除现有的nfo文件
                if nfo_path.exists():
                    nfo_path.unlink()
                    self.logger.info(f"  删除现有nfo文件")
                
                self.create_rich_nfo(video_info, nfo_path)
                self.logger.info(f"  ✅ nfo文件生成完成")
                
            except Exception as e:
                self.logger.error(f"  ❌ 生成nfo失败: {e}")
                import traceback
                self.logger.error(f"  详细错误: {traceback.format_exc()}")
    
    def create_rich_nfo(self, video_info, nfo_path):
        """创建丰富的nfo文件"""
        # 创建XML根元素
        movie = ET.Element('movie')
        
        # 基本信息 - 使用真实标题
        title = ET.SubElement(movie, 'title')
        title.text = video_info.get('title', 'Unknown')
        
        originaltitle = ET.SubElement(movie, 'originaltitle')
        originaltitle.text = video_info.get('title', 'Unknown')
        
        # 完整标题
        if video_info.get('fulltitle') and video_info.get('fulltitle') != video_info.get('title'):
            sorttitle = ET.SubElement(movie, 'sorttitle')
            sorttitle.text = video_info.get('fulltitle')
        
        # 描述
        plot = ET.SubElement(movie, 'plot')
        description = video_info.get('description', '')
        if description:
            plot.text = description
        else:
            plot.text = video_info.get('title', 'No description available')
        
        # 上传者信息
        uploader = video_info.get('uploader', '')
        uploader_id = video_info.get('uploader_id', '')
        
        if uploader:
            # 导演字段
            director = ET.SubElement(movie, 'director')
            director.text = uploader
            
            # 制片人字段
            producer = ET.SubElement(movie, 'producer')
            producer.text = uploader
            
            # 演员信息
            actor = ET.SubElement(movie, 'actor')
            name = ET.SubElement(actor, 'name')
            name.text = uploader
            role = ET.SubElement(actor, 'role')
            role.text = 'UP主'
            if uploader_id:
                profile = ET.SubElement(actor, 'profile')
                profile.text = f"https://space.bilibili.com/{uploader_id}"
        
        # 时长
        duration = video_info.get('duration')
        if duration:
            runtime = ET.SubElement(movie, 'runtime')
            runtime.text = str(int(duration / 60))  # 转换为分钟
            
            # 文件信息
            fileinfo = ET.SubElement(movie, 'fileinfo')
            streamdetails = ET.SubElement(fileinfo, 'streamdetails')
            video_stream = ET.SubElement(streamdetails, 'video')
            
            duration_ms = ET.SubElement(video_stream, 'durationinseconds')
            duration_ms.text = str(int(duration))
        
        # 上传日期
        upload_date = video_info.get('upload_date')
        if upload_date and len(upload_date) >= 8:
            try:
                formatted_date = f"{upload_date[:4]}-{upload_date[4:6]}-{upload_date[6:8]}"
                premiered = ET.SubElement(movie, 'premiered')
                premiered.text = formatted_date
                
                year = ET.SubElement(movie, 'year')
                year.text = upload_date[:4]
                
                dateadded = ET.SubElement(movie, 'dateadded')
                dateadded.text = formatted_date
            except:
                pass
        
        # 统计信息
        view_count = video_info.get('view_count')
        if view_count:
            playcount = ET.SubElement(movie, 'playcount')
            playcount.text = str(view_count)
            
            # 用观看数生成评分
            if view_count > 100000:
                rating_value = 9.0
            elif view_count > 50000:
                rating_value = 8.0
            elif view_count > 10000:
                rating_value = 7.0
            elif view_count > 1000:
                rating_value = 6.0
            else:
                rating_value = 5.0
            
            rating = ET.SubElement(movie, 'rating')
            rating.text = str(rating_value)
        
        # 点赞数
        like_count = video_info.get('like_count')
        if like_count:
            votes = ET.SubElement(movie, 'votes')
            votes.text = str(like_count)
        
        # 评论数
        comment_count = video_info.get('comment_count')
        if comment_count:
            criticrating = ET.SubElement(movie, 'criticrating')
            criticrating.text = str(min(100, comment_count * 10))  # 转换为百分制
        
        # 标签
        tags = video_info.get('tags', [])
        if tags:
            for tag in tags[:10]:  # 限制标签数量
                tag_elem = ET.SubElement(movie, 'tag')
                tag_elem.text = str(tag)
        
        # 分类
        categories = video_info.get('categories', [])
        if categories:
            for category in categories:
                genre = ET.SubElement(movie, 'genre')
                genre.text = str(category)
        
        # 添加默认分类
        genre_bilibili = ET.SubElement(movie, 'genre')
        genre_bilibili.text = 'Bilibili'
        
        # 合集信息
        playlist_title = video_info.get('playlist_title', '')
        if playlist_title:
            set_elem = ET.SubElement(movie, 'set')
            set_name = ET.SubElement(set_elem, 'name')
            set_name.text = playlist_title
        
        # 网站信息
        website = ET.SubElement(movie, 'website')
        website.text = video_info.get('webpage_url', '')
        
        # 唯一ID
        uniqueid = ET.SubElement(movie, 'uniqueid')
        uniqueid.set('type', 'bilibili')
        uniqueid.text = video_info.get('id', '')
        
        # 缩略图
        thumbnail_url = video_info.get('thumbnail')
        if thumbnail_url:
            thumb = ET.SubElement(movie, 'thumb')
            thumb.text = thumbnail_url
            
            fanart = ET.SubElement(movie, 'fanart')
            fanart_thumb = ET.SubElement(fanart, 'thumb')
            fanart_thumb.text = thumbnail_url
        
        # 视频质量信息
        width = video_info.get('width')
        height = video_info.get('height')
        fps = video_info.get('fps')
        vcodec = video_info.get('vcodec')
        
        if width or height or fps or vcodec:
            fileinfo = movie.find('fileinfo')
            if fileinfo is None:
                fileinfo = ET.SubElement(movie, 'fileinfo')
            
            streamdetails = fileinfo.find('streamdetails')
            if streamdetails is None:
                streamdetails = ET.SubElement(fileinfo, 'streamdetails')
            
            video_stream = streamdetails.find('video')
            if video_stream is None:
                video_stream = ET.SubElement(streamdetails, 'video')
            
            # 视频编码
            if vcodec:
                codec = ET.SubElement(video_stream, 'codec')
                codec.text = str(vcodec)
            
            # 分辨率
            if width:
                width_elem = ET.SubElement(video_stream, 'width')
                width_elem.text = str(width)
            
            if height:
                height_elem = ET.SubElement(video_stream, 'height')
                height_elem.text = str(height)
            
            # 帧率
            if fps:
                fps_elem = ET.SubElement(video_stream, 'framerate')
                fps_elem.text = str(fps)
        
        # 格式化XML并保存
        self.indent_xml(movie)
        tree = ET.ElementTree(movie)
        tree.write(nfo_path, encoding='utf-8', xml_declaration=True)
        
        self.logger.info(f"    ✓ 生成丰富nfo: {nfo_path.name}")
    
    def generate_video_id_list(self, video_groups):
        """生成视频ID列表"""
        self.logger.info("\\n" + "="*50)
        self.logger.info("生成视频ID列表...")
        self.logger.info("="*50)
        
        # 收集所有视频ID
        video_ids = []
        video_details = []
        
        for group in video_groups:
            video_id = group['video_id']
            title = group['title']
            video_info = group['video_info']
            safe_title = group.get('safe_title') or self.sanitize_filename(title)
            
            video_ids.append(video_id)
            
            # 详细信息
            detail = {
                'id': video_id,
                'title': title,
                'safe_filename': safe_title,
                'uploader': video_info.get('uploader', ''),
                'upload_date': video_info.get('upload_date', ''),
                'duration': video_info.get('duration', 0),
                'view_count': video_info.get('view_count', 0),
                'like_count': video_info.get('like_count', 0),
                'webpage_url': video_info.get('webpage_url', ''),
                'playlist_title': video_info.get('playlist_title', ''),
                'playlist_index': video_info.get('playlist_index', 0)
            }
            video_details.append(detail)
        
        # 保存简单ID列表（用于增量更新）
        id_list_file = self.directory / 'downloaded_video_ids.txt'
        with open(id_list_file, 'w', encoding='utf-8') as f:
            for video_id in sorted(video_ids):
                f.write(f"{video_id}\\n")
        
        self.logger.info(f"✓ 保存视频ID列表: {id_list_file.name} ({len(video_ids)}个)")
        
        # 保存详细信息（JSON格式）
        details_file = self.directory / 'video_details.json'
        with open(details_file, 'w', encoding='utf-8') as f:
            json.dump(video_details, f, ensure_ascii=False, indent=2)
        
        self.logger.info(f"✓ 保存视频详情: {details_file.name}")
        
        # 生成统计报告
        self.generate_statistics_report(video_details)
    
    def generate_statistics_report(self, video_details):
        """生成统计报告"""
        report_file = self.directory / 'collection_report.txt'
        
        total_videos = len(video_details)
        total_duration = sum(detail.get('duration', 0) for detail in video_details)
        total_views = sum(detail.get('view_count', 0) for detail in video_details)
        total_likes = sum(detail.get('like_count', 0) for detail in video_details)
        
        # 按年份统计
        year_stats = {}
        for detail in video_details:
            upload_date = detail.get('upload_date', '')
            if len(upload_date) >= 4:
                year = upload_date[:4]
                year_stats[year] = year_stats.get(year, 0) + 1
        
        # 按UP主统计
        uploader_stats = {}
        for detail in video_details:
            uploader = detail.get('uploader', 'Unknown')
            uploader_stats[uploader] = uploader_stats.get(uploader, 0) + 1
        
        with open(report_file, 'w', encoding='utf-8') as f:
            f.write("B站合集统计报告\\n")
            f.write("=" * 50 + "\\n\\n")
            
            f.write(f"总视频数量: {total_videos}\\n")
            f.write(f"总时长: {total_duration // 3600}小时{(total_duration % 3600) // 60}分钟\\n")
            f.write(f"总观看数: {total_views:,}\\n")
            f.write(f"总点赞数: {total_likes:,}\\n")
            f.write(f"平均观看数: {total_views // total_videos if total_videos > 0 else 0:,}\\n")
            f.write(f"平均点赞数: {total_likes // total_videos if total_videos > 0 else 0:,}\\n\\n")
            
            f.write("按年份统计:\\n")
            for year in sorted(year_stats.keys()):
                f.write(f"  {year}: {year_stats[year]}个视频\\n")
            f.write("\\n")
            
            f.write("按UP主统计:\\n")
            for uploader, count in sorted(uploader_stats.items(), key=lambda x: x[1], reverse=True):
                f.write(f"  {uploader}: {count}个视频\\n")
            
            f.write("\\n视频列表:\\n")
            for detail in video_details:
                f.write(f"  {detail['id']} - {detail['title']}\\n")
        
        self.logger.info(f"✓ 生成统计报告: {report_file.name}")
    
    def cleanup_files(self):
        """清理重复和临时文件"""
        self.logger.info("\\n" + "="*50)
        self.logger.info("清理临时文件...")
        self.logger.info("="*50)
        
        # 查找并删除重复的nfo文件
        nfo_files = list(self.directory.glob('*.nfo'))
        temp_nfo_files = [f for f in nfo_files if '.temp' in f.name or '.f30080' in f.name or f.name.startswith('0')]
        
        for temp_file in temp_nfo_files:
            try:
                temp_file.unlink()
                self.logger.info(f"删除临时文件: {temp_file.name}")
            except:
                pass
        
        # 清理空文件
        all_files = list(self.directory.glob('*'))
        for file in all_files:
            if file.is_file() and file.stat().st_size == 0:
                try:
                    file.unlink()
                    self.logger.info(f"删除空文件: {file.name}")
                except:
                    pass
        
        self.logger.info("✓ 清理完成")
    
    def sanitize_filename(self, filename):
        """清理文件名"""
        if not filename or filename.strip() == '':
            return 'untitled'
        
        # 移除非法字符
        filename = re.sub(r'[<>:"/\\\\|?*]', '_', filename)
        filename = re.sub(r'[\\r\\n\\t]', ' ', filename)
        filename = re.sub(r'\\s+', ' ', filename).strip()
        filename = filename.strip('. ')
        
        # 移除特殊字符但保留中文和基本标点
        filename = re.sub(r'[【】\\[\\]()（）]', '', filename)
        filename = re.sub(r'[!！@#$%^&*+={}|;:,.<>?~`]', '_', filename)
        
        # 限制长度
        if len(filename) > 80:
            filename = filename[:80].rsplit(' ', 1)[0]
        
        return filename or 'untitled'
    
    def indent_xml(self, elem, level=0):
        """格式化XML缩进"""
        i = "\\n" + level * "  "
        if len(elem):
            if not elem.text or not elem.text.strip():
                elem.text = i + "  "
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
            for elem in elem:
                self.indent_xml(elem, level + 1)
            if not elem.tail or not elem.tail.strip():
                elem.tail = i
        else:
            if level and (not elem.tail or not elem.tail.strip()):
                elem.tail = i

def main():
    parser = argparse.ArgumentParser(
        description='B站视频目录管理脚本 - 修复版本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
功能:
1. 从info.json生成丰富的nfo文件（使用真实标题）
2. 统一重命名文件为视频标题
3. 生成视频ID列表用于增量更新
4. 生成统计报告

示例:
  %(prog)s "/path/to/bilibili/collection"
        """
    )
    
    parser.add_argument('directory', help='B站视频目录路径')
    parser.add_argument('--dry-run', action='store_true', help='只显示操作，不实际执行')
    
    args = parser.parse_args()
    
    try:
        if args.dry_run:
            print("⚠️ 干运行模式：只显示操作，不实际执行")
        
        manager = BilibiliDirectoryManagerFixed(args.directory)
        
        if not args.dry_run:
            manager.process_directory()
        else:
            print("将要处理的目录:", args.directory)
            info_files = list(Path(args.directory).glob('*.info.json'))
            print(f"找到 {len(info_files)} 个视频文件")
            
            # 显示将要处理的文件
            for info_file in info_files[:5]:  # 只显示前5个
                try:
                    with open(info_file, 'r', encoding='utf-8') as f:
                        video_info = json.load(f)
                    title = video_info.get('title', 'Unknown')
                    print(f"  - {info_file.name} -> {title}")
                except:
                    print(f"  - {info_file.name} -> (无法读取标题)")
        
        print("\\n✅ 处理完成!")
        
    except Exception as e:
        print(f"\\n❌ 处理失败: {e}")
        import traceback
        print(f"详细错误: {traceback.format_exc()}")
        sys.exit(1)

if __name__ == '__main__':
    main()

