#!/usr/bin/env python3
"""
STRM部署验证脚本
验证STRM功能的部署环境和配置
"""

import os
import sys
import subprocess
import shutil
import json
import asyncio
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# 添加项目路径
sys.path.insert(0, '/Users/paramiao/development/bili_curator')


class DeploymentValidator:
    """部署验证器"""
    
    def __init__(self):
        self.validation_results = []
        self.warnings = []
        self.errors = []
    
    def check_python_version(self) -> bool:
        """检查Python版本"""
        try:
            version = sys.version_info
            if version.major == 3 and version.minor >= 8:
                self.validation_results.append(f"✓ Python版本: {version.major}.{version.minor}.{version.micro}")
                return True
            else:
                self.errors.append(f"✗ Python版本过低: {version.major}.{version.minor}.{version.micro} (需要3.8+)")
                return False
        except Exception as e:
            self.errors.append(f"✗ Python版本检查失败: {e}")
            return False
    
    def check_ffmpeg_installation(self) -> bool:
        """检查FFmpeg安装"""
        try:
            result = subprocess.run(['ffmpeg', '-version'], 
                                  capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                # 解析版本信息
                version_line = result.stdout.split('\n')[0]
                self.validation_results.append(f"✓ FFmpeg已安装: {version_line}")
                return True
            else:
                self.errors.append("✗ FFmpeg未正确安装")
                return False
        except subprocess.TimeoutExpired:
            self.errors.append("✗ FFmpeg响应超时")
            return False
        except FileNotFoundError:
            self.errors.append("✗ FFmpeg未找到，请安装FFmpeg")
            return False
        except Exception as e:
            self.errors.append(f"✗ FFmpeg检查失败: {e}")
            return False
    
    def check_python_dependencies(self) -> bool:
        """检查Python依赖"""
        required_packages = [
            'fastapi',
            'uvicorn',
            'sqlalchemy',
            'pydantic',
            'httpx',
            'loguru',
            'apscheduler'
        ]
        
        missing_packages = []
        installed_packages = []
        
        for package in required_packages:
            try:
                __import__(package)
                installed_packages.append(package)
            except ImportError:
                missing_packages.append(package)
        
        if not missing_packages:
            self.validation_results.append(f"✓ Python依赖完整: {len(installed_packages)}个包已安装")
            return True
        else:
            self.errors.append(f"✗ 缺少Python依赖: {', '.join(missing_packages)}")
            return False
    
    def check_directory_structure(self) -> bool:
        """检查目录结构"""
        base_dir = Path('/Users/paramiao/development/bili_curator')
        required_dirs = [
            'bili_curator/app',
            'bili_curator/app/services',
            'bili_curator/app/api_endpoints',
            'bili_curator/app/database',
            'bili_curator/static',
            'tests',
            'docs'
        ]
        
        missing_dirs = []
        existing_dirs = []
        
        for dir_path in required_dirs:
            full_path = base_dir / dir_path
            if full_path.exists():
                existing_dirs.append(dir_path)
            else:
                missing_dirs.append(dir_path)
        
        if not missing_dirs:
            self.validation_results.append(f"✓ 目录结构完整: {len(existing_dirs)}个目录")
            return True
        else:
            self.errors.append(f"✗ 缺少目录: {', '.join(missing_dirs)}")
            return False
    
    def check_strm_modules(self) -> bool:
        """检查STRM模块"""
        strm_modules = [
            'bili_curator.app.services.strm_proxy_service',
            'bili_curator.app.services.strm_file_manager',
            'bili_curator.app.services.strm_downloader',
            'bili_curator.app.services.enhanced_downloader',
            'bili_curator.app.services.strm_performance_optimizer'
        ]
        
        missing_modules = []
        loaded_modules = []
        
        for module_name in strm_modules:
            try:
                __import__(module_name)
                loaded_modules.append(module_name.split('.')[-1])
            except ImportError as e:
                missing_modules.append(f"{module_name} ({e})")
        
        if not missing_modules:
            self.validation_results.append(f"✓ STRM模块完整: {len(loaded_modules)}个模块")
            return True
        else:
            self.errors.append(f"✗ STRM模块缺失: {missing_modules}")
            return False
    
    def check_configuration(self) -> bool:
        """检查配置文件"""
        config_checks = []
        
        # 检查.env文件
        env_file = Path('/Users/paramiao/development/bili_curator/.env')
        if env_file.exists():
            config_checks.append("✓ .env文件存在")
        else:
            self.warnings.append("⚠️  .env文件不存在，将使用默认配置")
        
        # 检查requirements.txt
        req_file = Path('/Users/paramiao/development/bili_curator/bili_curator/requirements.txt')
        if req_file.exists():
            config_checks.append("✓ requirements.txt存在")
        else:
            self.errors.append("✗ requirements.txt文件缺失")
            return False
        
        self.validation_results.extend(config_checks)
        return True
    
    def check_database_connection(self) -> bool:
        """检查数据库连接"""
        try:
            from bili_curator.app.database.models import Base
            from bili_curator.app.database.connection import get_database_url
            
            # 尝试导入模型
            self.validation_results.append("✓ 数据库模型导入成功")
            
            # 检查数据库URL
            db_url = get_database_url()
            if db_url:
                self.validation_results.append("✓ 数据库配置正确")
                return True
            else:
                self.errors.append("✗ 数据库配置错误")
                return False
                
        except Exception as e:
            self.errors.append(f"✗ 数据库连接检查失败: {e}")
            return False
    
    def check_port_availability(self) -> bool:
        """检查端口可用性"""
        import socket
        
        ports_to_check = [8000, 8888]  # 主应用端口和STRM代理端口
        available_ports = []
        occupied_ports = []
        
        for port in ports_to_check:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                result = sock.connect_ex(('localhost', port))
                if result == 0:
                    occupied_ports.append(port)
                else:
                    available_ports.append(port)
            except Exception:
                available_ports.append(port)  # 假设可用
            finally:
                sock.close()
        
        if not occupied_ports:
            self.validation_results.append(f"✓ 端口可用: {', '.join(map(str, available_ports))}")
            return True
        else:
            self.warnings.append(f"⚠️  端口被占用: {', '.join(map(str, occupied_ports))}")
            return True  # 不算错误，只是警告
    
    def check_disk_space(self) -> bool:
        """检查磁盘空间"""
        try:
            import shutil
            
            # 检查当前目录磁盘空间
            total, used, free = shutil.disk_usage('/')
            
            # 转换为GB
            free_gb = free / (1024**3)
            total_gb = total / (1024**3)
            
            if free_gb > 5:  # 至少5GB可用空间
                self.validation_results.append(f"✓ 磁盘空间充足: {free_gb:.1f}GB可用 / {total_gb:.1f}GB总计")
                return True
            else:
                self.warnings.append(f"⚠️  磁盘空间不足: {free_gb:.1f}GB可用，建议至少5GB")
                return True  # 不算错误，只是警告
                
        except Exception as e:
            self.warnings.append(f"⚠️  磁盘空间检查失败: {e}")
            return True
    
    def check_network_connectivity(self) -> bool:
        """检查网络连接"""
        import urllib.request
        
        test_urls = [
            'https://www.bilibili.com',
            'https://api.bilibili.com'
        ]
        
        successful_connections = []
        failed_connections = []
        
        for url in test_urls:
            try:
                response = urllib.request.urlopen(url, timeout=10)
                if response.getcode() == 200:
                    successful_connections.append(url)
                else:
                    failed_connections.append(f"{url} (状态码: {response.getcode()})")
            except Exception as e:
                failed_connections.append(f"{url} ({e})")
        
        if successful_connections:
            self.validation_results.append(f"✓ 网络连接正常: {len(successful_connections)}个URL可访问")
            if failed_connections:
                self.warnings.append(f"⚠️  部分URL无法访问: {failed_connections}")
            return True
        else:
            self.errors.append(f"✗ 网络连接失败: {failed_connections}")
            return False
    
    def check_file_permissions(self) -> bool:
        """检查文件权限"""
        test_dir = tempfile.mkdtemp()
        
        try:
            # 测试创建文件
            test_file = os.path.join(test_dir, 'test.txt')
            with open(test_file, 'w') as f:
                f.write('test')
            
            # 测试读取文件
            with open(test_file, 'r') as f:
                content = f.read()
            
            # 测试删除文件
            os.remove(test_file)
            
            self.validation_results.append("✓ 文件权限正常")
            return True
            
        except Exception as e:
            self.errors.append(f"✗ 文件权限检查失败: {e}")
            return False
        finally:
            shutil.rmtree(test_dir, ignore_errors=True)
    
    async def run_all_checks(self) -> Dict[str, any]:
        """运行所有检查"""
        print("🔍 开始部署环境验证")
        print("=" * 50)
        
        checks = [
            ("Python版本", self.check_python_version),
            ("FFmpeg安装", self.check_ffmpeg_installation),
            ("Python依赖", self.check_python_dependencies),
            ("目录结构", self.check_directory_structure),
            ("STRM模块", self.check_strm_modules),
            ("配置文件", self.check_configuration),
            ("数据库连接", self.check_database_connection),
            ("端口可用性", self.check_port_availability),
            ("磁盘空间", self.check_disk_space),
            ("网络连接", self.check_network_connectivity),
            ("文件权限", self.check_file_permissions)
        ]
        
        passed_checks = 0
        total_checks = len(checks)
        
        for check_name, check_func in checks:
            print(f"\n📋 检查: {check_name}")
            try:
                if check_func():
                    passed_checks += 1
                    print(f"✅ {check_name} 通过")
                else:
                    print(f"❌ {check_name} 失败")
            except Exception as e:
                print(f"❌ {check_name} 异常: {e}")
                self.errors.append(f"{check_name} 检查异常: {e}")
        
        # 生成报告
        print("\n" + "=" * 50)
        print("📊 验证结果总结")
        print("=" * 50)
        
        print(f"\n✅ 通过的检查 ({passed_checks}/{total_checks}):")
        for result in self.validation_results:
            print(f"  {result}")
        
        if self.warnings:
            print(f"\n⚠️  警告 ({len(self.warnings)}):")
            for warning in self.warnings:
                print(f"  {warning}")
        
        if self.errors:
            print(f"\n❌ 错误 ({len(self.errors)}):")
            for error in self.errors:
                print(f"  {error}")
        
        # 计算总体状态
        if self.errors:
            status = "FAILED"
            print(f"\n🚨 部署验证失败: 发现 {len(self.errors)} 个错误")
        elif self.warnings:
            status = "WARNING"
            print(f"\n⚠️  部署验证通过但有警告: {len(self.warnings)} 个警告")
        else:
            status = "PASSED"
            print(f"\n🎉 部署验证完全通过!")
        
        return {
            'status': status,
            'passed_checks': passed_checks,
            'total_checks': total_checks,
            'validation_results': self.validation_results,
            'warnings': self.warnings,
            'errors': self.errors,
            'recommendations': self.get_recommendations()
        }
    
    def get_recommendations(self) -> List[str]:
        """获取部署建议"""
        recommendations = []
        
        if self.errors:
            recommendations.append("🔧 修复所有错误后重新运行验证")
        
        if any("FFmpeg" in error for error in self.errors):
            recommendations.append("📦 安装FFmpeg: https://ffmpeg.org/download.html")
        
        if any("依赖" in error for error in self.errors):
            recommendations.append("📦 安装Python依赖: pip install -r bili_curator/requirements.txt")
        
        if any("端口" in warning for warning in self.warnings):
            recommendations.append("🔌 检查端口占用，必要时修改配置文件中的端口设置")
        
        if any("磁盘" in warning for warning in self.warnings):
            recommendations.append("💾 清理磁盘空间或扩容存储")
        
        if any("网络" in error for error in self.errors):
            recommendations.append("🌐 检查网络连接和防火墙设置")
        
        # 通用建议
        recommendations.extend([
            "📖 查看部署指南: docs/v7/STRM_DEPLOYMENT_GUIDE.md",
            "🔍 运行功能测试: python tests/test_strm_basic.py",
            "📊 监控系统资源使用情况",
            "🔒 配置适当的安全设置"
        ])
        
        return recommendations


async def main():
    """主函数"""
    validator = DeploymentValidator()
    result = await validator.run_all_checks()
    
    # 保存验证结果
    result_file = 'deployment_validation_result.json'
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    
    print(f"\n📄 验证结果已保存到: {result_file}")
    
    if result['recommendations']:
        print(f"\n💡 建议:")
        for rec in result['recommendations']:
            print(f"  {rec}")
    
    # 返回适当的退出码
    if result['status'] == 'FAILED':
        return 1
    else:
        return 0


if __name__ == '__main__':
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
