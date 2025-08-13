#!/usr/bin/env python3
"""
导入现有视频文件到数据库 - V6重新设计版
基于视频ID的简洁导入逻辑
"""

import os
import sys
import json
from pathlib import Path
from datetime import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.models import Video, Base

def find_video_files(download_dir):
    """
    扫描目录，找到所有视频文件及其对应的JSON元数据
    返回: [(video_file_path, json_file_path, video_id), ...]
    """
    video_pairs = []
    download_path = Path(download_dir)
    
    print(f"🔍 扫描目录: {download_path}")
    
    # 递归查找所有视频文件
    video_extensions = ['.mp4', '.mkv', '.flv', '.webm']
    for video_file in download_path.rglob("*"):
        if video_file.is_file() and video_file.suffix.lower() in video_extensions:
            # 查找对应的JSON文件
            json_file = None
            video_id = None
            
            # 策略1: 查找同名的.json文件
            potential_json = video_file.with_suffix('.json')
            if potential_json.exists():
                json_file = potential_json
            
            # 策略2: 查找同名的.info.json文件
            if not json_file:
                potential_info_json = video_file.parent / f"{video_file.stem}.info.json"
                if potential_info_json.exists():
                    json_file = potential_info_json
            
            # 如果找到JSON文件，提取视频ID
            if json_file:
                try:
                    with open(json_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    
                    if isinstance(metadata, dict) and 'id' in metadata:
                        video_id = metadata['id']
                        video_pairs.append((video_file, json_file, video_id))
                        print(f"✅ 找到视频: {video_file.name} -> {video_id}")
                    else:
                        print(f"⚠️  JSON格式不正确: {json_file.name}")
                        
                except Exception as e:
                    print(f"❌ JSON解析失败: {json_file.name} - {e}")
            else:
                print(f"⚠️  未找到JSON元数据: {video_file.name}")
    
    print(f"📊 总计找到 {len(video_pairs)} 个有效视频文件")
    return video_pairs

def import_to_database(video_pairs):
    """
    将视频信息导入数据库
    """
    # 数据库连接
    engine = create_engine('sqlite:////app/data/bilibili_curator.db')
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    
    imported_count = 0
    skipped_count = 0
    
    try:
        for video_file, json_file, video_id in video_pairs:
            # 检查是否已存在
            existing = session.query(Video).filter_by(bilibili_id=video_id).first()
            if existing:
                print(f"⏭️  跳过已存在: {video_id}")
                skipped_count += 1
                continue
            
            # 读取完整的JSON元数据
            try:
                with open(json_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                
                # 处理上传日期
                upload_date = None
                upload_date_str = metadata.get('upload_date')
                if upload_date_str:
                    try:
                        if len(upload_date_str) == 8:
                            upload_date = datetime.strptime(upload_date_str, '%Y%m%d')
                        else:
                            upload_date = datetime.fromisoformat(upload_date_str.replace('Z', '+00:00'))
                    except:
                        pass
                
                # 查找缩略图文件
                thumbnail_file = None
                for ext in ['.jpg', '.jpeg', '.png', '.webp']:
                    potential_thumb = video_file.with_suffix(ext)
                    if potential_thumb.exists():
                        thumbnail_file = potential_thumb
                        break
                
                # 创建视频记录
                video = Video(
                    bilibili_id=video_id,
                    title=metadata.get('title', ''),
                    uploader=metadata.get('uploader', ''),
                    uploader_id=metadata.get('uploader_id', ''),
                    duration=metadata.get('duration', 0),
                    upload_date=upload_date,
                    description=metadata.get('description', ''),
                    tags=json.dumps(metadata.get('tags', []), ensure_ascii=False),
                    video_path=str(video_file),
                    json_path=str(json_file),
                    thumbnail_path=str(thumbnail_file) if thumbnail_file else None,
                    file_size=video_file.stat().st_size,
                    view_count=metadata.get('view_count', 0),
                    downloaded=True,
                    downloaded_at=datetime.fromtimestamp(video_file.stat().st_mtime)
                )
                
                session.add(video)
                imported_count += 1
                print(f"✅ 导入成功: {metadata.get('title', video_id)}")
                
            except Exception as e:
                print(f"❌ 导入失败: {video_id} - {e}")
                continue
        
        session.commit()
        print(f"\n🎉 导入完成!")
        print(f"✅ 成功导入: {imported_count} 个视频")
        print(f"⏭️  跳过重复: {skipped_count} 个视频")
        
    except Exception as e:
        session.rollback()
        print(f"❌ 数据库操作失败: {e}")
        raise
    finally:
        session.close()

def main():
    """主函数"""
    download_dir = "/app/downloads"
    
    if len(sys.argv) > 1:
        download_dir = sys.argv[1]
    
    print("🎬 === Bilibili视频导入工具 V6重新设计版 ===")
    
    # 扫描所有合集或指定目录
    all_video_pairs = []
    
    # 如果指定了特定目录，只扫描该目录
    if len(sys.argv) > 1:
        video_pairs = find_video_files(download_dir)
        all_video_pairs.extend(video_pairs)
    else:
        # 扫描所有已知合集
        for collection in ["合集·AI·科技·商业·新知", 
                          "合集·AI·科技·商业·新知-2023-2025_6",
                          "合集·乔布斯合集"]:
            collection_path = f"{download_dir}/{collection}"
            if Path(collection_path).exists():
                print(f"\n📁 扫描合集: {collection}")
                video_pairs = find_video_files(collection_path)
                all_video_pairs.extend(video_pairs)
    
    if not all_video_pairs:
        print("❌ 未找到任何有效的视频文件")
        return
    
    print(f"\n📊 总计找到 {len(all_video_pairs)} 个视频文件")
    
    # 确认导入
    response = input("❓ 是否继续导入到数据库？(y/N): ")
    if response.lower() != 'y':
        print("❌ 取消导入")
        return
    
    # 导入到数据库
    import_to_database(all_video_pairs)

if __name__ == "__main__":
    main()
