"""Build an offline UOWD public snapshot from approved saved HTML and sources.csv."""
import argparse, csv, hashlib, json, re
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urljoin
from bs4 import BeautifulSoup

HOST='www.uowdubai.ac.ae'
def official(url):
    p=urlsplit(url)
    return p.scheme=='https' and p.netloc==HOST and not p.username

def clean(node):
    return re.sub(r'\s+', ' ', node.get_text(' ', strip=True)).strip()

def grid(table):
    """Expand rowspans and colspans, so every row retains its own column labels."""
    spans={}; result=[]
    for tr in table.find_all('tr'):
        row={}; following={}
        for i,(value,left) in spans.items():
            row[i]=value
            if left>1: following[i]=(value,left-1)
        col=0
        for cell in tr.find_all(['th','td'], recursive=False):
            while col in row: col+=1
            value=clean(cell); width=int(cell.get('colspan',1)); height=int(cell.get('rowspan',1))
            for i in range(col,col+width):
                row[i]=value
                if height>1: following[i]=(value,height-1)
            col+=width
        spans=following
        if row: result.append([row.get(i,'') for i in range(max(row)+1)])
    return result

def extract(raw,url,date):
    soup=BeautifulSoup(raw,'html.parser')
    title=clean(soup.title) if soup.title else ''
    if any(x in title.lower() for x in ('just a moment','access denied','attention required','page not found','403 forbidden')) or soup.select_one('#challenge-form,#cf-challenge-running'):
        raise ValueError('Challenge or error page')
    canonical=soup.select_one('link[rel=canonical]')
    if not canonical or canonical.get('href','').rstrip('/')!=url.rstrip('/'):
        raise ValueError('Canonical URL does not match source mapping')
    root=soup.select_one('#block-uowd-content, main, [role=main], #main-content, .region-content')
    if not root: raise ValueError('No recognised content region')
    for e in root.select('script,style,nav,header,footer,aside,form,noscript,.breadcrumb,.nav,.tab-btn'):
        if e.parent: e.decompose()
    heading=clean(root.find('h1')) if root.find('h1') else title
    records=[]; programmes=[]
    def add(text,**extra):
        if text.strip(): records.append(dict(title=heading,url=url,text=text,retrieved_at=date,content_hash=hashlib.sha256(text.encode()).hexdigest(),**extra))
    for card in root.select('.card'):
        h=card.select_one('.card-header'); tables=card.find_all('table')
        if not h or not tables: continue
        programme=clean(h); programmes.append(programme)
        for t in tables:
            rows=grid(t)
            if len(rows)<2: continue
            headers=rows[0]
            for values in rows[1:]:
                if len(values)!=len(headers): raise ValueError('Inconsistent table width: '+programme)
                fields=dict(zip(headers,values))
                notes=[clean(p) for p in card.find_all('p') if clean(p)]
                text=programme+'\n'+'\n'.join(f'{k}: {v}' for k,v in fields.items())
                if notes: text+='\n'+'\n'.join(notes)
                add(text,kind='programme_fee',programme=programme,fields=fields)
        card.decompose()
    masters=[p for p in programmes if p.lower().startswith('master of ')]
    if masters: add("Master's programmes listed on this tuition fee page:\n"+'\n'.join(masters),kind='masters_list',programmes=masters)
    for box in root.select('.primaryBox'):
        h=box.find(['h2','h3','h4','h5','h6']); a=box.select_one('a[href]')
        if h and a:
            target=urljoin(url,a['href'])
            if official(target): add(f'{heading}\n{clean(h)}: {target}',kind='navigation_link',target_url=target)
        box.decompose()
    section=heading
    for node in root.find_all(['h1','h2','h3','h4','h5','h6','p','li','table']):
        if node.find_parent('table') or (node.name=='p' and node.find_parent('li')): continue
        if re.fullmatch('h[1-6]',node.name): section=clean(node) or section; continue
        if node.name=='li' and node.find('li'): continue
        if node.name=='table': body='\n'.join(' | '.join(row) for row in grid(node))
        else: body=clean(node)
        if len(body)<20: continue
        for offset in range(0,len(body),1200): add(section+'\n'+body[offset:offset+1450],kind='text')
    return records

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--input-dir',type=Path,required=True);ap.add_argument('--output',type=Path,required=True);args=ap.parse_args()
    root=args.input_dir.resolve(); records=[]
    with (root/'sources.csv').open(encoding='utf-8-sig',newline='') as f:
        for row in csv.DictReader(f):
            p=(root/row['filename'].strip()).resolve();url=row['url'].strip()
            if root not in p.parents or not official(url) or p.suffix.lower() not in ('.html','.htm'): raise ValueError('Invalid source mapping')
            date=row.get('retrieved_at','').strip() or datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()
            result=extract(p.read_bytes(),url,date)
            if not result: raise ValueError('No usable records: '+p.name)
            records+=result;print(p.name,len(result))
    if not records: raise ValueError('No records; snapshot unchanged')
    unique=list({(r['url'],r['content_hash']):r for r in records}.values())
    out=args.output;out.parent.mkdir(parents=True,exist_ok=True);tmp=out.with_suffix('.tmp');tmp.write_text(json.dumps({'retrieved_at':datetime.now(timezone.utc).isoformat(),'collection_method':'authorized manual HTML import; structured card tables','chunks':unique},ensure_ascii=False,indent=2),encoding='utf-8');tmp.replace(out);print('Saved',len(unique),'passages')
if __name__=='__main__': main()
