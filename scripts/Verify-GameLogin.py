"""Explicit local verification; does not claim or write mini-program success."""
import argparse
import base64
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from assistant.config import data_directory, now
from assistant.game_login import BrokerClient, classify_page, ensure_game_login
from assistant.desktop.vision import read_text
from assistant.platform import InstanceLock


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--run',action='store_true',help='Run the bounded login workflow')
    parser.add_argument('--restart',action='store_true',help='Explicitly permit a normal lobby exit before the login test')
    args=parser.parse_args()
    if args.restart and not args.run:
        parser.error('--restart requires --run')
    root=Path(__file__).resolve().parents[1]/'test-output'/'game-launcher'/now().strftime('verification-%Y%m%d-%H%M%S')
    root.mkdir(parents=True,exist_ok=True)
    lock=InstanceLock(data_directory()/'automation.lock')
    if not lock.acquire():
        raise RuntimeError('Another automation is running')
    def event(message):
        print(message,flush=True)
        with (root/'events.txt').open('a',encoding='utf-8') as out:
            out.write(now().isoformat()+' '+message+'\n')
    try:
        if args.restart or not args.run:
            with BrokerClient() as broker:
                observed=broker.request('observe')
                image=root/'before.png'; image.write_bytes(base64.b64decode(observed['png']))
                state=classify_page(observed['kind'],[item.text for item in read_text(image)])
                if args.restart:
                    if state.status!='ready':
                        raise RuntimeError('Restart refused: current page is not a recognized lobby')
                    event('Lobby verified; requesting the explicitly authorized normal game exit')
                    event(json.dumps(broker.request('act:exit_lobby')))
        if args.restart:
            import psutil
            for _ in range(30):
                if not any((p.info['name'] or '').lower()=='narakabladepoint.exe' for p in psutil.process_iter(['name'])):
                    break
                time.sleep(1)
            else:
                raise RuntimeError('Game did not exit; no force termination or repeated close request was made')
            time.sleep(1)
        if args.run:
            state=ensure_game_login(root,event)
        report=dict(state.__dict__,evidence=str(root),miniprogram_claim_verified=False)
        (root/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(report,ensure_ascii=True),flush=True)
        return 0 if state.status=='ready' else 1
    finally:
        lock.close()


if __name__=='__main__':
    raise SystemExit(main())
