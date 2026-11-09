from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import urllib.request,hashlib,json,time
OUT=Path(__file__).parent
LOCAL=OUT/'published/luma_1'
BASE='https://isla-demo.vercel.app'
def verify(path):
    url=BASE+'/robots/luma_1/'+path.name+'?review=20261109-'+str(int(time.time()))
    request=urllib.request.Request(url,headers={'Cache-Control':'no-cache'})
    with urllib.request.urlopen(request,timeout=40) as response: data=response.read()
    expected=hashlib.sha256(path.read_bytes()).hexdigest();got=hashlib.sha256(data).hexdigest()
    assert got==expected,(path.name,'live file differs')
    return {'file':path.name,'sha256':got,'bytes':len(data),'matched':True}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=4) as ex:results=list(ex.map(verify,sorted(LOCAL.iterdir())))
    with urllib.request.urlopen(BASE,timeout=30) as response:page=response.read().decode()
    assert 'index-raUtFIgk.js' in page,'Unexpected client build'
    result={'deployment':'dpl_5hPjtLqRD9eXvdxxojXhinUpxxee','url':BASE,'client_build':'index-raUtFIgk.js','files':results}
    (OUT/'live_verification.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
