"""읽기 전용 DB 품질·모델 표본 현황. python cafe_db_report.py [--output 경로]."""
import argparse
import json
from pathlib import Path
import pandas as pd
import listings
import price_quality

PRIORITIES = ['커피머신','커피 그라인더','제빙기','블렌더','업소용 냉장고','업소용 냉동고',
              '쇼케이스 냉장고','오븐','정수기','온수기','작업대','싱크대','포스기','키오스크']

def report():
    raw=pd.read_csv(listings.LISTINGS,dtype=str,keep_default_na=False)
    frame=listings.read(); verified=price_quality.eligible(listings.production(frame))
    groups=verified.groupby(['item','manufacturer','model','spec','condition','price_basis']).size().reset_index(name='verified_count')
    groups['minimum_target']=3; groups['recommended_target']=5
    groups['minimum_gap']=(3-groups.verified_count).clip(lower=0)
    groups['recommended_gap']=(5-groups.verified_count).clip(lower=0)
    groups=groups.to_dict('records')
    return {'raw_records':len(raw),'normalized_records':len(frame),'duplicate_urls':int(raw.url.duplicated().sum()),
            'missing_model':int((raw.model=='').sum()),'missing_manufacturer':int((raw.manufacturer=='').sum()),
            'verified_eligible':len(verified),'pending_or_ineligible':len(frame)-len(verified),
            'item_counts':raw.groupby('item').size().to_dict(),
            'source_counts':raw.groupby('source').size().to_dict(),
            'verified_groups':groups,'missing_priority_items':[i for i in PRIORITIES if i not in set(raw.item)],
            'caution':'게시가/낙찰가/판매 완료 게시가, 사양과 상태가 다른 그룹은 합산하지 않음. 최소 3건은 정확도 보장 아님.'}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--output');a=p.parse_args()
    result=json.dumps(report(),ensure_ascii=False,indent=2)
    if a.output:Path(a.output).write_text(result+'\n',encoding='utf-8')
    print(result)
