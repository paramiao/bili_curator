#!/usr/bin/env python3
"""
STRM缩略图补充脚本
用于为现有STRM文件补充缺失的缩略图
"""

import asyncio
import aiohttp
import os
import re
from pathlib import Path
from loguru import logger
import xml.etree.ElementTree as ET

class STRMThumbnailFixer:
    def __init__(self, strm_base_path="/app/strm"):
        self.strm_base_path = Path(strm_base_path)
        self.session = None
        
    async def start(self):
        """启动HTTP会话"""
        self.session = aiohttp.ClientSession()
        
    async def stop(self):
        """停止HTTP会话"""
        if self.session:
            await self.session.close()
    
    async def get_bilibili_thumbnail(self, bvid: str) -> str:
        """获取B站视频缩略图URL"""
        try:
            url = f"https://api.bilibili.com/x/web-interface/view?bvid={bvid}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            
            async with self.session.get(url, headers=headers) as response:
                if response.status == 200:
                    data = await response.json()
                    if data.get("code") == 0:
                        video_data = data.get("data", {})
                        return video_data.get("pic", "")
                        
        except Exception as e:
            logger.error(f"获取缩略图URL失败: {bvid}, {e}")
            
        return ""
    
    async def download_thumbnail(self, thumbnail_url: str, save_path: Path) -> bool:
        """下载缩略图"""
        try:
            async with self.session.get(thumbnail_url) as response:
                if response.status == 200:
                    save_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(save_path, 'wb') as f:
                        f.write(await response.read())
                    logger.info(f"下载缩略图成功: {save_path}")
                    return True
                    
        except Exception as e:
            logger.error(f"下载缩略图失败: {thumbnail_url}, {e}")
            
        return False
    
    def update_nfo_file(self, nfo_path: Path, thumbnail_filename: str):
        """更新NFO文件中的缩略图引用"""
        try:
            # 读取NFO文件
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            
            # 更新thumb和fanart标签
            thumb_elem = root.find('thumb')
            fanart_elem = root.find('fanart')
            
            if thumb_elem is not None:
                thumb_elem.text = thumbnail_filename
            else:
                thumb_elem = ET.SubElement(root, 'thumb')
                thumb_elem.text = thumbnail_filename
                
            if fanart_elem is not None:
                fanart_elem.text = thumbnail_filename
            else:
                fanart_elem = ET.SubElement(root, 'fanart')
                fanart_elem.text = thumbnail_filename
            
            # 保存更新后的NFO文件
            tree.write(nfo_path, encoding='utf-8', xml_declaration=True)
            logger.info(f"更新NFO文件: {nfo_path}")
            
        except Exception as e:
            logger.error(f"更新NFO文件失败: {nfo_path}, {e}")
    
    def extract_bvid_from_nfo(self, nfo_path: Path) -> str:
        """从NFO文件中提取BVID"""
        try:
            tree = ET.parse(nfo_path)
            root = tree.getroot()
            
            uniqueid_elem = root.find('.//uniqueid[@type="bilibili"]')
            if uniqueid_elem is not None:
                return uniqueid_elem.text or ""
                
        except Exception as e:
            logger.error(f"解析NFO文件失败: {nfo_path}, {e}")
            
        return ""
    
    async def fix_missing_thumbnails(self):
        """修复缺失的缩略图"""
        logger.info("开始扫描STRM目录...")
        
        # 查找所有NFO文件
        nfo_files = list(self.strm_base_path.rglob("*.nfo"))
        logger.info(f"找到 {len(nfo_files)} 个NFO文件")
        
        fixed_count = 0
        skipped_count = 0
        
        for nfo_path in nfo_files:
            try:
                # 检查是否已有缩略图
                video_dir = nfo_path.parent
                jpg_files = list(video_dir.glob("*.jpg"))
                
                if jpg_files:
                    logger.debug(f"跳过已有缩略图: {nfo_path}")
                    skipped_count += 1
                    continue
                
                # 从NFO文件提取BVID
                bvid = self.extract_bvid_from_nfo(nfo_path)
                if not bvid:
                    logger.warning(f"无法从NFO文件提取BVID: {nfo_path}")
                    continue
                
                # 获取缩略图URL
                thumbnail_url = await self.get_bilibili_thumbnail(bvid)
                if not thumbnail_url:
                    logger.warning(f"无法获取缩略图URL: {bvid}")
                    continue
                
                # 生成缩略图文件名
                video_name = nfo_path.stem
                thumbnail_path = video_dir / f"{video_name}.jpg"
                
                # 下载缩略图
                if await self.download_thumbnail(thumbnail_url, thumbnail_path):
                    # 更新NFO文件
                    self.update_nfo_file(nfo_path, f"{video_name}.jpg")
                    fixed_count += 1
                    logger.info(f"修复完成: {bvid} -> {thumbnail_path}")
                
                # 避免请求过于频繁
                await asyncio.sleep(0.5)
                
            except Exception as e:
                logger.error(f"处理NFO文件失败: {nfo_path}, {e}")
        
        logger.info(f"缩略图修复完成: 修复 {fixed_count} 个, 跳过 {skipped_count} 个")

async def main():
    """主函数"""
    fixer = STRMThumbnailFixer()
    
    try:
        await fixer.start()
        await fixer.fix_missing_thumbnails()
    finally:
        await fixer.stop()

if __name__ == "__main__":
    asyncio.run(main())
