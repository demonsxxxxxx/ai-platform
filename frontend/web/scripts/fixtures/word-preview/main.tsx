import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import i18n from 'i18next';
import { initReactI18next } from 'react-i18next';
import WordPreview from '../../../src/components/documents/previews/WordPreview';
import { createSyntheticWordDocument } from '../../../src/components/documents/previews/__tests__/wordPreviewFixture';
import '../../../src/styles/tailwind.css';
import '../../../src/styles/base.css';

await i18n.use(initReactI18next).init({lng:'en',fallbackLng:'en',resources:{en:{translation:{imageViewer:{zoomIn:'Zoom in',zoomOut:'Zoom out',reset:'Reset'}}}},interpolation:{escapeValue:false}});
const translate=(key:string)=>key;
export function Fixture() {
  const [bytes,setBytes]=useState<ArrayBuffer|null>(null);
  const [status,setStatus]=useState('ready');
  const load=async(landscape:boolean,fallback=false)=>{
    document.documentElement.classList.toggle('dark',fallback);setStatus('preparing');
    setBytes(await createSyntheticWordDocument({landscape,fallback}));setStatus(landscape?'landscape':'portrait');
  };
  return <main style={{padding:20}}>
    <h1>Synthetic Word CI fixture</h1>
    <button id="portrait" onClick={()=>void load(false)}>Portrait</button>
    <button id="landscape" onClick={()=>void load(true)}>Landscape</button>
    <button id="dark-fallback" onClick={()=>void load(false,true)}>Real dark fallback</button>
    <button id="close" onClick={()=>{setBytes(null);setStatus('closed');}}>Close</button>
    <output id="status">{status}</output>
    <div id="preview-host" style={{height:700,width:960}}>
      {bytes&&<WordPreview arrayBuffer={bytes} t={translate}/>}
    </div>
  </main>;
}
createRoot(document.getElementById('root')!).render(<Fixture/>);
