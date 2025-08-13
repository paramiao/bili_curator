#!/usr/bin/env python3
"""
清理废弃脚本工具
识别并安全移除不再需要的脚本文件
"""

import os
import shutil
from pathlib import Path
from datetime import datetime

class ScriptCleanup:
    def __init__(self, package_dir):
        self.package_dir = Path(package_dir)
        self.backup_dir = self.package_dir / 'backup_obsolete'
        
        # 定义废弃脚本列表
        self.obsolete_scripts = [
            'fix_rename_issue.py',  # 重命名功能已集成到主脚本
            'bilibili_directory_manager_fixed.py',  # 与主下载器功能重复
        ]
        
        # 可能废弃的脚本（需要用户确认）
        self.potentially_obsolete = [
            # 暂时保留，可能还有用
        ]
    
    def create_backup_dir(self):
        """创建备份目录"""
        if not self.backup_dir.exists():
            self.backup_dir.mkdir(exist_ok=True)
            print(f"✅ 创建备份目录: {self.backup_dir}")
    
    def backup_and_remove_script(self, script_name):
        """备份并移除脚本"""
        script_path = self.package_dir / script_name
        
        if not script_path.exists():
            print(f"⚠️  脚本不存在: {script_name}")
            return False
        
        # 创建备份
        backup_path = self.backup_dir / script_name
        try:
            shutil.copy2(script_path, backup_path)
            print(f"📦 已备份: {script_name} -> backup_obsolete/")
            
            # 删除原文件
            script_path.unlink()
            print(f"🗑️  已删除: {script_name}")
            return True
            
        except Exception as e:
            print(f"❌ 处理失败 {script_name}: {e}")
            return False
    
    def analyze_script_usage(self):
        """分析脚本使用情况"""
        print("📊 脚本使用分析:")
        print("=" * 50)
        
        all_scripts = [f for f in self.package_dir.iterdir() 
                      if f.is_file() and f.suffix == '.py']
        
        for script in all_scripts:
            status = "🟢 保留"
            reason = "核心功能"
            
            if script.name in self.obsolete_scripts:
                status = "🔴 废弃"
                if script.name == 'fix_rename_issue.py':
                    reason = "重命名功能已集成到V5版本"
                elif script.name == 'bilibili_directory_manager_fixed.py':
                    reason = "功能与主下载器重复"
            elif script.name in self.potentially_obsolete:
                status = "🟡 待定"
                reason = "需要进一步评估"
            
            print(f"{status} {script.name:<40} - {reason}")
    
    def cleanup(self, dry_run=True):
        """执行清理"""
        print(f"\n{'🔍 预览模式' if dry_run else '🚀 执行清理'}")
        print("=" * 50)
        
        if not dry_run:
            self.create_backup_dir()
        
        removed_count = 0
        
        for script_name in self.obsolete_scripts:
            if dry_run:
                script_path = self.package_dir / script_name
                if script_path.exists():
                    print(f"将删除: {script_name}")
                    removed_count += 1
                else:
                    print(f"不存在: {script_name}")
            else:
                if self.backup_and_remove_script(script_name):
                    removed_count += 1
        
        print(f"\n{'预计' if dry_run else '实际'}清理 {removed_count} 个废弃脚本")
        
        if dry_run:
            print("\n要执行实际清理，请运行: python cleanup_obsolete_scripts.py --execute")
    
    def create_cleanup_report(self):
        """创建清理报告"""
        report_path = self.package_dir / 'CLEANUP_REPORT.md'
        
        report_content = f"""# 脚本清理报告

生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

## 废弃脚本列表

### 已删除的脚本
"""
        
        for script_name in self.obsolete_scripts:
            script_path = self.package_dir / script_name
            status = "✅ 已删除" if not script_path.exists() else "❌ 仍存在"
            
            if script_name == 'fix_rename_issue.py':
                reason = "重命名功能已集成到V5版本的sanitize_filename方法中"
            elif script_name == 'bilibili_directory_manager_fixed.py':
                reason = "功能与bilibili_collection_downloader_v5.py重复"
            else:
                reason = "功能已废弃"
            
            report_content += f"""
#### {script_name}
- 状态: {status}
- 原因: {reason}
- 备份位置: backup_obsolete/{script_name}
"""
        
        report_content += """
## 当前活跃脚本

### 核心脚本
- `bilibili_collection_downloader_v5.py` - 主要下载器（V5改进版）
- `bilibili_collection_downloader_v4.py` - 原V4版本（保留作为备用）
- `bilibili_incremental_downloader.py` - 增量下载器
- `batch_download_v4.py` - 批量下载器

### 工具脚本
- `diagnose_bilibili.py` - 诊断工具
- `cleanup_obsolete_scripts.py` - 清理工具

### 配置文件
- `collections_config_v4.json` - 合集配置
- `requirements.txt` - 依赖文件

## V5版本改进

### 主要新特性
1. **智能增量下载** - 基于目录现有文件自动检测
2. **统一文件命名** - 支持标题和ID两种命名策略
3. **增强NFO文件** - 使用JSON元数据创建丰富的NFO内容
4. **并发下载** - 支持多线程并发下载
5. **文件完整性验证** - 检查已下载文件的完整性
6. **改进错误处理** - 更好的重试机制和错误恢复

### 使用建议
- 新项目使用V5版本
- 现有项目可以逐步迁移到V5
- V4版本保留作为稳定备用版本
"""
        
        try:
            with open(report_path, 'w', encoding='utf-8') as f:
                f.write(report_content)
            print(f"📄 清理报告已生成: {report_path}")
        except Exception as e:
            print(f"❌ 生成报告失败: {e}")

def main():
    import argparse
    
    parser = argparse.ArgumentParser(description='清理废弃脚本工具')
    parser.add_argument('--execute', action='store_true', help='执行实际清理（默认为预览模式）')
    parser.add_argument('--package-dir', default='.', help='包目录路径')
    
    args = parser.parse_args()
    
    cleanup = ScriptCleanup(args.package_dir)
    
    print("🧹 B站下载器脚本清理工具")
    print("=" * 50)
    
    # 分析脚本使用情况
    cleanup.analyze_script_usage()
    
    # 执行清理
    cleanup.cleanup(dry_run=not args.execute)
    
    # 生成报告
    if args.execute:
        cleanup.create_cleanup_report()

if __name__ == '__main__':
    main()
