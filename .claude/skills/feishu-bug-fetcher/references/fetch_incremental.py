#!/usr/bin/env python3
"""
增量获取飞书缺陷数据

支持三种模式:
1. mcporter 模式: 通过 mcporter 获取全量 ID 列表, 对比本地数据, 获取新增缺陷详情
2. 手动 ID 模式: 用户提供新增 bug ID 列表, 脚本获取详情
3. ID 范围探测: 从最大已知 ID 开始向上探测, 寻找新 bug

用法:
    python3 fetch_incremental.py --ids 123,456        # 指定 ID 列表获取详情
    python3 fetch_incremental.py --ids 123,456,789    # 手动指定 ID
    python3 fetch_incremental.py --probe 1000         # 探测 max_id 上方 1000 个 ID
    python3 fetch_incremental.py --recent-days 7      # 获取近 7 天更新的缺陷
"""

import json, os, sys, time, subprocess, argparse
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Set, Optional, Tuple

# === Configuration ===
SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

# Add scripts dir to path for config loading
sys.path.insert(0, str(SCRIPTS_DIR))

from config import get_feishu_config, get_output_config

# Load feishu config
fs_cfg = get_feishu_config()
PROJECT_KEY = fs_cfg['project_key']
PLUGIN_ID = fs_cfg['plugin_id']
PLUGIN_SECRET = fs_cfg['plugin_secret']
USER_KEY = fs_cfg['user_key']

# Data directory from config
out_cfg = get_output_config()
DATA_DIR = Path(os.path.expanduser(out_cfg.get('base_dir', '~/.openviking/workspace/feishu-bugs')))

# API constants
API_BASE = f"https://project.feishu.cn/open_api/{PROJECT_KEY}"
TOKEN_URL = "https://project.feishu.cn/open_api/authen/plugin_token"
BATCH_SIZE = 50  # Direct API 单次最大查询数 (超过50返回空)
DETAIL_BATCH_SIZE = 50  # 详情获取批次大小
REQUEST_TIMEOUT = 30
MCPORTER_TIMEOUT = 120  # mcporter 调用超时 (秒)

# File paths
INDEX_FILE = DATA_DIR / 'bugs_index_full.json'
DETAILS_FILE = DATA_DIR / 'bugs_details_full.json'
BACKUP_DIR = DATA_DIR / 'backups'


class IncrementalFetcher:
    """增量获取飞书缺陷数据"""
    
    def __init__(self):
        self._token = None
        self._token_expires = 0
        self.stats = {
            'existing_ids': 0,
            'new_ids': 0,
            'fetched_details': 0,
            'failed_details': 0,
            'updated_details': 0,
        }
    
    def get_plugin_token(self) -> str:
        """获取 plugin token (自动刷新)"""
        now = time.time()
        if self._token and now < self._token_expires - 300:  # 提前 5 分钟刷新
            return self._token
        
        resp = json.loads(subprocess.run(
            ['curl', '-s', '-X', 'POST', TOKEN_URL,
             '-H', 'Content-Type: application/json',
             '-d', json.dumps({
                 "plugin_id": PLUGIN_ID,
                 "plugin_secret": PLUGIN_SECRET,
                 "type": 0
             })],
            capture_output=True, text=True, timeout=10
        ).stdout)
        
        self._token = resp.get('data', {}).get('token')
        self._token_expires = now + 7200  # 2 小时有效期
        return self._token
    
    def get_headers(self) -> dict:
        """获取 API 请求头"""
        return {
            "x-plugin-token": self.get_plugin_token(),
            "x-user-key": USER_KEY,
            "Content-Type": "application/json"
        }
    
    def load_existing_data(self) -> Tuple[Set[str], List, List]:
        """加载已有数据, 返回 (IDs, index_data, details_data)"""
        # Load index
        with open(INDEX_FILE) as f:
            index_data = json.load(f)
        existing_ids = set(str(b['id']) for b in index_data)
        
        # Load details
        with open(DETAILS_FILE) as f:
            details_data = json.load(f)
        
        if not isinstance(details_data, list):
            details_data = []
        
        self.stats['existing_ids'] = len(existing_ids)
        return existing_ids, index_data, details_data
    
    def backup_files(self):
        """备份当前数据文件"""
        BACKUP_DIR.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        for src in [INDEX_FILE, DETAILS_FILE]:
            if src.exists():
                dst = BACKUP_DIR / f"{src.stem}_{timestamp}{src.suffix}"
                import shutil
                shutil.copy2(src, dst)
                print(f"  备份: {dst.name}")
    
    def query_bugs_by_ids(self, bug_ids: List[int]) -> List[dict]:
        """通过 Direct API 批量查询 bug 信息"""
        all_bugs = []
        
        for i in range(0, len(bug_ids), BATCH_SIZE):
            batch = bug_ids[i:i+BATCH_SIZE]
            resp = json.loads(subprocess.run(
                ['curl', '-s', '-X', 'POST',
                 f"{API_BASE}/work_item/issue/query",
                 '-H', f'x-plugin-token: {self.get_plugin_token()}',
                 '-H', f'x-user-key: {USER_KEY}',
                 '-H', 'Content-Type: application/json',
                 '-d', json.dumps({"work_item_ids": batch})],
                capture_output=True, text=True, timeout=REQUEST_TIMEOUT
            ).stdout)
            
            bugs = resp.get('data', [])
            all_bugs.extend(bugs)
            time.sleep(0.2)  # 限速
        
        return all_bugs
    
    def try_mcporter_list(self) -> Optional[List[dict]]:
        """尝试通过 mcporter 获取全量 bug 列表"""
        print("\n[尝试] mcporter search_by_mql 获取全量列表...")
        
        try:
            result = subprocess.run(
                ['mcporter', 'call', 'meego', 'search_by_mql', '--args',
                 json.dumps({
                     "project_key": PROJECT_KEY,
                     "mql": f"SELECT work_item_id, name, work_item_status FROM {PROJECT_KEY}.issue LIMIT 10000"
                 })],
                capture_output=True, text=True, timeout=MCPORTER_TIMEOUT
            )
            
            if result.returncode == 0 and result.stdout.strip():
                output = result.stdout.strip()
                # 解析 mcporter 输出 (可能是 markdown 包裹的 JSON)
                if output.startswith('{'):
                    data = json.loads(output)
                    bugs = data.get('data', data.get('result', []))
                    if isinstance(bugs, list):
                        print(f"  mcporter 返回 {len(bugs)} 条记录")
                        return bugs
                elif '[' in output:
                    # 尝试提取 JSON 数组
                    start = output.index('[')
                    end = output.rindex(']') + 1
                    bugs = json.loads(output[start:end])
                    print(f"  mcporter 返回 {len(bugs)} 条记录")
                    return bugs
            
            print(f"  mcporter 无有效返回 (exit={result.returncode})")
            return None
            
        except subprocess.TimeoutExpired:
            print("  mcporter 超时, 跳过此模式")
            return None
        except Exception as e:
            print(f"  mcporter 异常: {e}")
            return None
    
    def probe_id_range(self, start_id: int, count: int) -> List[int]:
        """探测 ID 范围内的有效 bug"""
        print(f"\n[探测] 从 ID {start_id} 开始向上探测 {count} 个 ID...")
        
        new_ids = []
        for i in range(0, count, BATCH_SIZE):
            batch_ids = list(range(start_id + i, start_id + min(i + BATCH_SIZE, count)))
            batch = [int(x) for x in batch_ids]
            
            bugs = self.query_bugs_by_ids(batch)
            found = [b['id'] for b in bugs if b.get('id')]
            new_ids.extend(found)
            
            if found:
                print(f"  ID {start_id + i}~{start_id + i + len(batch_ids)}: 找到 {len(found)} 个 bug")
            else:
                print(f"  ID {start_id + i}~{start_id + i + len(batch_ids)}: 无新 bug")
            
            time.sleep(0.3)
        
        return new_ids
    
    def fetch_incremental_by_ids(self, new_ids: List[int], index_data: List, details_data: List) -> Tuple[List, List]:
        """获取新增 bug 的详情并更新本地数据"""
        if not new_ids:
            print("\n无新增缺陷")
            return index_data, details_data
        
        print(f"\n[获取] {len(new_ids)} 个新增缺陷的详情...")
        
        # 获取详情 (每次 50 个)
        for i in range(0, len(new_ids), DETAIL_BATCH_SIZE):
            batch = new_ids[i:i+DETAIL_BATCH_SIZE]
            bugs = self.query_bugs_by_ids(batch)
            
            for bug in bugs:
                bug_id = str(bug.get('id'))
                if not bug_id:
                    continue
                
                # 检查是否已存在
                existing_detail = None
                for idx, d in enumerate(details_data):
                    did = d.get('id')
                    if not did and 'work_item_attribute' in d:
                        did = d['work_item_attribute'].get('work_item_id')
                    if str(did) == bug_id:
                        existing_detail = d
                        break
                
                if existing_detail:
                    # 更新已有详情
                    details_data[details_data.index(existing_detail)] = bug
                    self.stats['updated_details'] += 1
                    print(f"  更新: {bug_id}")
                else:
                    # 添加新详情
                    details_data.append(bug)
                    self.stats['fetched_details'] += 1
                    print(f"  新增: {bug_id}")
                
                # 添加到索引
                index_entry = {
                    'id': bug_id,
                    'name': bug.get('name', ''),
                    'status': bug.get('status', '')
                }
                index_data.append(index_entry)
                self.stats['new_ids'] += 1
            
            time.sleep(0.3)
        
        return index_data, details_data
    
    def save_data(self, index_data: List, details_data: List):
        """保存更新后的数据"""
        self.backup_files()
        
        with open(INDEX_FILE, 'w', encoding='utf-8') as f:
            json.dump(index_data, f, ensure_ascii=False, indent=2)
        
        with open(DETAILS_FILE, 'w', encoding='utf-8') as f:
            json.dump(details_data, f, ensure_ascii=False, indent=2)
        
        print(f"\n[保存] 数据已更新:")
        print(f"  bugs_index_full.json: {len(index_data)} 条")
        print(f"  bugs_details_full.json: {len(details_data)} 条")
    
    def print_stats(self):
        """打印统计信息"""
        print(f"\n{'='*50}")
        print(f"增量获取完成")
        print(f"{'='*50}")
        print(f"已有缺陷: {self.stats['existing_ids']}")
        print(f"新增缺陷: {self.stats['new_ids']}")
        print(f"获取详情: {self.stats['fetched_details']}")
        print(f"更新详情: {self.stats['updated_details']}")
        print(f"获取失败: {self.stats['failed_details']}")
        print(f"{'='*50}")
    
    def run_auto(self):
        """自动模式: 尝试 mcporter, 失败则探测 ID 范围"""
        print("=== 自动模式: 增量获取飞书缺陷 ===\n")
        
        # 加载已有数据
        existing_ids, index_data, details_data = self.load_existing_data()
        print(f"已有缺陷: {len(existing_ids)} 条")
        
        # 获取最大 ID
        max_id = max(int(x) for x in existing_ids)
        print(f"最大已知 ID: {max_id}")
        
        # 尝试 mcporter
        bugs_list = self.try_mcporter_list()
        
        if bugs_list is not None:
            # mcporter 成功, 提取 ID 并对比
            remote_ids = set()
            for bug in bugs_list:
                wid = bug.get('work_item_id') or bug.get('id')
                if wid:
                    remote_ids.add(str(wid))
            
            new_ids = remote_ids - existing_ids
            print(f"远程缺陷: {len(remote_ids)} 条")
            print(f"新增缺陷: {len(new_ids)} 条")
            
            if new_ids:
                index_data, details_data = self.fetch_incremental_by_ids(
                    [int(x) for x in new_ids], index_data, details_data
                )
        else:
            # mcporter 失败, 探测 ID 范围
            print("\n[回退] 使用 ID 范围探测模式...")
            new_ids = self.probe_id_range(max_id, 10000)
            
            if new_ids:
                # 去重
                new_ids = [x for x in new_ids if str(x) not in existing_ids]
                index_data, details_data = self.fetch_incremental_by_ids(
                    new_ids, index_data, details_data
                )
        
        # 保存数据
        self.save_data(index_data, details_data)
        self.print_stats()
    
    def run_manual_ids(self, ids: List[int]):
        """手动 ID 模式"""
        print(f"=== 手动模式: 获取指定 {len(ids)} 个缺陷的详情 ===\n")
        
        existing_ids, index_data, details_data = self.load_existing_data()
        
        # 过滤已有 ID
        new_ids = [x for x in ids if str(x) not in existing_ids]
        print(f"新增缺陷: {len(new_ids)}/{len(ids)}")
        
        if new_ids:
            index_data, details_data = self.fetch_incremental_by_ids(
                new_ids, index_data, details_data
            )
        
        self.save_data(index_data, details_data)
        self.print_stats()
    
    def run_probe(self, count: int):
        """ID 范围探测模式"""
        print(f"=== 探测模式: 探测最大 ID 上方 {count} 个 ID ===\n")
        
        existing_ids, index_data, details_data = self.load_existing_data()
        max_id = max(int(x) for x in existing_ids)
        print(f"最大已知 ID: {max_id}")
        
        new_ids = self.probe_id_range(max_id, count)
        new_ids = [x for x in new_ids if str(x) not in existing_ids]
        
        if new_ids:
            index_data, details_data = self.fetch_incremental_by_ids(
                new_ids, index_data, details_data
            )
        
        self.save_data(index_data, details_data)
        self.print_stats()


def main():
    parser = argparse.ArgumentParser(description='增量获取飞书缺陷数据')
    parser.add_argument('--ids', type=str, help='逗号分隔的 bug ID 列表')
    parser.add_argument('--probe', type=int, help='探测最大 ID 上方的 ID 数量')
    parser.add_argument('--recent-days', type=int, help='获取近 N 天更新的缺陷')
    parser.add_argument('--mode', choices=['auto', 'mcporter', 'probe'], default='auto',
                       help='获取模式 (默认: auto)')
    
    args = parser.parse_args()
    
    fetcher = IncrementalFetcher()
    
    if args.ids:
        ids = [int(x.strip()) for x in args.ids.split(',') if x.strip()]
        fetcher.run_manual_ids(ids)
    elif args.probe:
        fetcher.run_probe(args.probe)
    elif args.mode == 'mcporter':
        # 仅使用 mcporter 模式
        fetcher.run_auto()  # auto 模式会先尝试 mcporter
    else:
        fetcher.run_auto()


if __name__ == '__main__':
    main()
