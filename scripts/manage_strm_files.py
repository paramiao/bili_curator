#!/usr/bin/env python3
"""
STRM文件管理工具
- 批量更新现有STRM文件为相对路径
- 支持Docker容器内外路径
- 提供备份和回滚功能
"""

import os
import re
import shutil
import argparse
from pathlib import Path
from datetime import datetime
from typing import List, Tuple, Dict


class STRMFileManager:
    def __init__(self, strm_path: str, backup_enabled: bool = True):
        self.strm_path = Path(strm_path)
        self.backup_enabled = backup_enabled
        self.backup_dir = self.strm_path.parent / "strm_backup"
        
    def find_strm_files(self) -> List[Path]:
        """查找所有STRM文件"""
        strm_files = []
        if self.strm_path.exists():
            strm_files.extend(self.strm_path.rglob("*.strm"))
        return strm_files
    
    def create_backup(self, files: List[Path]) -> str:
        """创建备份"""
        if not self.backup_enabled or not files:
            return ""
            
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = self.backup_dir / f"backup_{timestamp}"
        backup_path.mkdir(parents=True, exist_ok=True)
        
        for file_path in files:
            relative_path = file_path.relative_to(self.strm_path)
            backup_file = backup_path / relative_path
            backup_file.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(file_path, backup_file)
        
        return str(backup_path)
    
    def analyze_strm_file(self, file_path: Path) -> Dict:
        """分析STRM文件内容"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read().strip()
            
            # 检查不同URL格式
            patterns = {
                'absolute_ip': r'http://(\d+\.\d+\.\d+\.\d+):(\d+)/api/strm/stream/([A-Za-z0-9]+)',
                'absolute_localhost': r'http://localhost:(\d+)/api/strm/stream/([A-Za-z0-9]+)',
                'absolute_domain': r'https?://([^/]+)/api/strm/stream/([A-Za-z0-9]+)',
                'relative': r'^/api/strm/stream/([A-Za-z0-9]+)$',
                'invalid': r'.*'
            }
            
            for pattern_type, pattern in patterns.items():
                match = re.match(pattern, content)
                if match:
                    if pattern_type == 'absolute_ip':
                        return {
                            'type': pattern_type,
                            'content': content,
                            'ip': match.group(1),
                            'port': match.group(2),
                            'bilibili_id': match.group(3),
                            'needs_update': True
                        }
                    elif pattern_type == 'absolute_localhost':
                        return {
                            'type': pattern_type,
                            'content': content,
                            'port': match.group(1),
                            'bilibili_id': match.group(2),
                            'needs_update': True
                        }
                    elif pattern_type == 'absolute_domain':
                        return {
                            'type': pattern_type,
                            'content': content,
                            'domain': match.group(1),
                            'bilibili_id': match.group(2),
                            'needs_update': True
                        }
                    elif pattern_type == 'relative':
                        return {
                            'type': pattern_type,
                            'content': content,
                            'bilibili_id': match.group(1),
                            'needs_update': False
                        }
            
            return {
                'type': 'invalid',
                'content': content,
                'needs_update': False,
                'error': '无法识别的STRM格式'
            }
            
        except Exception as e:
            return {
                'type': 'error',
                'content': '',
                'needs_update': False,
                'error': str(e)
            }
    
    def update_strm_file(self, file_path: Path, analysis: Dict, dry_run: bool = False) -> bool:
        """更新单个STRM文件为相对路径"""
        if not analysis['needs_update']:
            return False
        
        if 'bilibili_id' not in analysis:
            return False
        
        new_content = f"/api/strm/stream/{analysis['bilibili_id']}"
        
        if not dry_run:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write(new_content)
                return True
            except Exception:
                return False
        
        return True  # dry_run模式
    
    def batch_update(self, dry_run: bool = False, verbose: bool = False) -> Dict:
        """批量更新STRM文件"""
        strm_files = self.find_strm_files()
        
        if not strm_files:
            return {
                'total': 0,
                'updated': 0,
                'already_relative': 0,
                'errors': 0,
                'backup_path': ''
            }
        
        # 分析所有文件
        analyses = {}
        for file_path in strm_files:
            analyses[file_path] = self.analyze_strm_file(file_path)
        
        # 统计需要更新的文件
        files_to_update = [f for f, a in analyses.items() if a['needs_update']]
        
        # 创建备份
        backup_path = ""
        if files_to_update and not dry_run:
            backup_path = self.create_backup(files_to_update)
        
        # 执行更新
        updated_count = 0
        already_relative_count = 0
        error_count = 0
        
        for file_path, analysis in analyses.items():
            if analysis['type'] == 'error':
                error_count += 1
                if verbose:
                    print(f"❌ [ERROR] {file_path}: {analysis['error']}")
            elif analysis['type'] == 'invalid':
                error_count += 1
                if verbose:
                    print(f"❌ [INVALID] {file_path}: {analysis['error']}")
            elif analysis['needs_update']:
                success = self.update_strm_file(file_path, analysis, dry_run)
                if success:
                    updated_count += 1
                    status = "🔄 [DRY-RUN]" if dry_run else "✅ [UPDATED]"
                    if verbose:
                        print(f"{status} {file_path}")
                        print(f"    {analysis['type']}: {analysis['content']}")
                        print(f"    → /api/strm/stream/{analysis['bilibili_id']}")
                else:
                    error_count += 1
                    if verbose:
                        print(f"❌ [FAILED] {file_path}")
            else:
                already_relative_count += 1
                if verbose:
                    print(f"✓ [SKIP] {file_path}: 已经是相对路径")
        
        return {
            'total': len(strm_files),
            'updated': updated_count,
            'already_relative': already_relative_count,
            'errors': error_count,
            'backup_path': backup_path
        }


def main():
    parser = argparse.ArgumentParser(description='STRM文件管理工具')
    parser.add_argument('--strm-path', default='/app/strm', 
                       help='STRM文件目录路径 (默认: /app/strm)')
    parser.add_argument('--dry-run', action='store_true', 
                       help='预览模式，不实际修改文件')
    parser.add_argument('--verbose', action='store_true', 
                       help='详细输出')
    parser.add_argument('--no-backup', action='store_true', 
                       help='禁用备份功能')
    
    args = parser.parse_args()
    
    print(f"🔧 STRM文件管理工具")
    print(f"📁 目标路径: {args.strm_path}")
    
    manager = STRMFileManager(args.strm_path, backup_enabled=not args.no_backup)
    
    # 执行批量更新
    result = manager.batch_update(dry_run=args.dry_run, verbose=args.verbose)
    
    print(f"\n📊 处理结果:")
    print(f"   总文件数: {result['total']}")
    print(f"   已更新: {result['updated']}")
    print(f"   已是相对路径: {result['already_relative']}")
    print(f"   错误文件: {result['errors']}")
    
    if result['backup_path']:
        print(f"   备份路径: {result['backup_path']}")
    
    if args.dry_run and result['updated'] > 0:
        print(f"\n💡 预览模式完成，实际更新请运行:")
        print(f"   python3 {__file__} --strm-path {args.strm_path}")


if __name__ == "__main__":
    main()
