import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import WordPreview from '../../../src/components/documents/previews/WordPreview';
import { prepareWordPreviewDocument } from '../../../src/components/documents/previews/wordHtmlPreview';
import { createSyntheticWordDocument } from '../../../src/components/documents/previews/__tests__/wordPreviewFixture';
import '../../../src/styles/tailwind.css';

await i18n.use(initReactI18next).init({lng:'en',fallbackLng:'en',resources:{en:{translation:{imageViewer:{zoomIn:'Zoom in',zoomOut:'Zoom out',reset:'Reset'}}}},interpolation:{escapeValue:false}});
const translate=(key:string)=>key;
export function Fixture() {
  const [bytes,setBytes]=useState<ArrayBuffer|null>(null);
  const [fallback,setFallback]=useState(false);
  const [status,setStatus]=useState('ready');
  const load=async(landscape:boolean)=>{
    setFallback(false);setStatus('preparing');
    setBytes(await createSyntheticWordDocument({landscape}));setStatus(landscape?'landscape':'portrait');
  };
  return <main style={{padding:20}}>
    <h1>Synthetic Word CI fixture</h1>
    <button id="portrait" onClick={()=>void load(false)}>Portrait</button>
    <button id="landscape" onClick={()=>void load(true)}>Landscape</button>
    <button id="dark-fallback" onClick={()=>{setBytes(null);setFallback(true);setStatus('dark fallback');}}>Dark fallback frame</button>
    <button id="close" onClick={()=>{setBytes(null);setFallback(false);setStatus('closed');}}>Close</button>
    <output id="status">{status}</output>
    <div id="preview-host" style={{height:700,width:960}}>
      {bytes&&<WordPreview arrayBuffer={bytes} t={translate}/>}
      {fallback&&<iframe title="Dark fallback" sandbox="" referrerPolicy="no-referrer" style={{height:600,width:816}} srcDoc={prepareWordPreviewDocument({html:'<p>Dark synthetic fallback</p><script>parent.__wordProbeCompromised=true</script><a href="https://synthetic.invalid/fallback-nav">Blocked navigation</a>',dark:true,className:'word-preview-content dark',styles:'.word-preview-content.dark{color:#e5e7eb}'})}/>}
    </div>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
