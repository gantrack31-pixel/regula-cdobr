import fs from 'node:fs/promises';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import { createRequire } from 'node:module';

const root = process.cwd();
const skill = process.env.PRESENTATIONS_SKILL;
const runtimeModules=process.env.RUNTIME_NODE_MODULES || process.env.CODEX_PRIMARY_RUNTIME_NODE_MODULES;
if (!skill || !process.env.RUNTIME_PYTHON || !runtimeModules) throw new Error('Set PRESENTATIONS_SKILL, RUNTIME_PYTHON and RUNTIME_NODE_MODULES to the bundled runtime paths.');
const runtimeRequire=createRequire(path.join(runtimeModules,'package.json'));
const { Presentation, PresentationFile }=await import(pathToFileURL(runtimeRequire.resolve('@oai/artifact-tool')).href);
const output = path.join(root, 'output');
const work = path.join(root, 'tmp', 'presentation');
await fs.mkdir(work, {recursive:true});
await fs.mkdir(output, {recursive:true});
const p = Presentation.create({slideSize:{width:1280,height:720}});
const C = {navy:'#101C35',blue:'#2459E0',ink:'#16243B',muted:'#64738A',line:'#DCE3EF',light:'#F4F7FC',white:'#FFFFFF',mint:'#96F0CF'};
function box(s,x,y,w,h,fill,line='none',lw=0) { return s.shapes.add({geometry:'rect',position:{left:x,top:y,width:w,height:h},fill,line:{fill:line,width:lw}}); }
function text(s,t,x,y,w,h,size=24,color=C.ink,bold=false) {
  const a=s.shapes.add({geometry:'textbox',position:{left:x,top:y,width:w,height:h},fill:'none',line:{fill:'none',width:0}});
  a.text=t; a.text.style={typeface:'Arial',fontSize:size,color,bold}; return a;
}
function page(number,title,kicker='РЕГУЛА / ЦДОБР') {
  const s=p.slides.add(); s.background.fill=C.white;
  text(s,kicker,64,36,1000,24,14,C.blue,true);
  text(s,title,64,88,1145,86,38,C.ink,true);
  box(s,64,667,1152,1,C.line);
  text(s,'Учебный прототип на материалах организаторов',64,680,970,20,12,C.muted);
  text(s,String(number).padStart(2,'0'),1160,678,56,22,14,C.muted,true);
  return s;
}
async function screenshot(s,name,x,y,w,h) {
  const file=path.join(root,'tmp','demo','screens',name+'.png');
  await fs.access(file);
  const bytes = await fs.readFile(file);
  const blob = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  s.images.add({blob,contentType:'image/png',alt:'Реальный интерфейс Регулы: '+name,fit:'contain',position:{left:x,top:y,width:w,height:h}});
}

// 1: deliberately sparse cover; all text is editable.
{
  const s=p.slides.add(); s.background.fill=C.navy;
  text(s,'ХАКАТОН / КЕЙС ЦДОБР',64,48,1130,30,16,C.mint,true);
  text(s,'Регула',64,132,1140,130,90,C.white,true);
  text(s,'Помощник по делопроизводству\nс проверяемыми источниками',68,282,1100,126,38,C.white);
  box(s,68,467,1144,1,'#40506C');
  text(s,'10',68,502,225,72,54,C.mint,true);
  text(s,'исходных документов',68,583,290,40,20,C.white);
  text(s,'7',465,502,225,72,54,C.mint,true);
  text(s,'модулей Word',465,583,290,40,20,C.white);
  text(s,'Локально',858,514,340,60,38,C.mint,true);
  text(s,'LLM, поиск и данные',858,583,340,40,20,C.white);
  s.speakerNotes.text='0:00–0:20. Сотруднику нужно не просто найти документ, а выполнить задачу по нему. Регула связывает ответ с конкретным пунктом, помогает собрать проект и объясняет маршрут визирования. Мы использовали учебные материалы организаторов.';
}
// 2: evidence before model fluency.
{
  const s=page(2,'Каждый ответ можно проверить');
  await screenshot(s,'answer',64,185,820,444);
  text(s,'Пункт и страница',920,202,295,38,25,C.blue,true);
  text(s,'Дословный фрагмент и ссылка на использованную редакцию.',920,250,285,118,22);
  text(s,'Без догадок',920,395,295,38,25,C.blue,true);
  text(s,'Уточнение задачи, честное отсутствие сведений, история диалога.',920,443,285,144,22);
  s.speakerNotes.text='0:20–0:55. В живом приложении задайте вопрос: «В какой срок регистрируется поступивший документ?» Раскройте цитату пункта 5.6. Покажите страницу и редакцию. Не заявляйте, что проверка цитат гарантирует смысловую правильность. При вопросе вне базы помощник должен сказать, чего не знает.';
}
// 3: actual form and export.
{
  const s=page(3,'Проекты документов по исходным модулям');
  await screenshot(s,'documents',64,185,820,444);
  text(s,'Сообщить факты',920,198,290,38,25,C.blue,true);
  text(s,'Заполнение из сообщения или в форме. Недостающие поля видны.',920,245,290,120,21);
  text(s,'Проверить и скачать',920,399,290,76,25,C.blue,true);
  text(s,'Исходный модуль, условные пункты, таблицы участников, настоящий DOCX.',920,485,290,132,21);
  s.speakerNotes.text='0:55–1:30. Откройте рабочую группу, сообщите цель, участников и сроки. Объясните: помощник не выдумывает реквизиты. Пустые поля отмечены, перед выгрузкой есть предпросмотр. После этого покажите маршрут ввода ИС: юридическое подразделение, ДОУ, ИТ и ИБ с основаниями. Маршрут — рекомендация, а не состоявшееся согласование.';
}
// 4: a native editable architecture diagram with explicit process meaning.
{
  const s=page(4,'База знаний и локальная архитектура');
  const names=['Документ','Черновик\nредакции','Проверка\nкуратором','Активация','Ответы\nи проекты'];
  const pipeline=names.map((name,i)=>{
    const x=64+i*235;
    const node=box(s,x,200,208,94,i===4?C.blue:C.light);
    text(s,name,x+15,216,178,70,23,i===4?C.white:C.ink,true);
    return node;
  });
  const connect=(from,to)=>s.shapes.connect(from,to,{kind:'straight',fromSide:'right',toSide:'left',line:{fill:C.muted,width:2},tail:{type:'arrow',width:'sm',length:'sm'}});
  pipeline.slice(1).forEach((node,i)=>connect(pipeline[i],node));
  text(s,'Локальный контур',64,355,1100,44,29,C.ink,true);
  const components=[['React','Рабочее пространство'],['FastAPI','Доступ и правила'],['Поиск','BM25 и эмбеддинги'],['Ollama','Локальная LLM']];
  const architecture=components.map(([name,detail],i)=>{
    const x=64+i*294;
    const node=box(s,x,418,264,100,C.light,C.line,1);
    text(s,name,x+15,432,234,36,25,C.blue,true);
    text(s,detail,x+15,476,234,26,17,C.muted);
    return node;
  });
  architecture.slice(1).forEach((node,i)=>connect(architecture[i],node));
  text(s,'SQLite хранит проекты и журнал действий. Файлы сохраняют историю редакций.',64,552,1150,42,23,C.ink);
  text(s,'Генератор DOCX использует модули. Правила визирования работают отдельно от LLM.',64,601,1150,39,21,C.ink);
  s.speakerNotes.text='1:30–2:10. Бонусный сценарий из видео организаторов: куратор загружает demo/new-module.txt, просматривает и активирует. После этого сразу появляется новая форма. Для автоматической формы нужны явные маркеры полей. Версии сохраняются; старый черновик нельзя незаметно выгрузить по измененному шаблону. Коротко объясните схему: данные и модель локальны, доступ проверяется до поиска. Правила маршрута детерминированы и обновляются вместе с тестами.';
}
// 5: factual boundaries, no invented KPI.
{
  const s=page(5,'Работающий прототип, который можно проверить');
  text(s,'Что демонстрируем',64,195,535,42,29,C.blue,true);
  const proof=['Ответ с цитатой и версией','Семь исходных модулей и экспорт DOCX','Проверка проекта и объяснение виз','Новый модуль через интерфейс'];
  proof.forEach((v,i)=>text(s,String(i+1).padStart(2,'0')+'   '+v,64,260+i*67,570,57,22,C.ink));
  text(s,'Что проверяем',690,195,526,42,29,C.blue,true);
  text(s,'Автотесты API, прав доступа, версий, правил и документов.\n\nРеальные вопросы к локальной модели и проверка интерфейса.',690,259,515,210,24,C.ink);
  text(s,'Пилот в организации',690,504,526,37,26,C.blue,true);
  text(s,'SSO, ИИС ДО, подпись, экспертная оценка ответов и защита хранения.',690,553,520,83,22,C.ink);
  s.speakerNotes.text='2:10–3:00. Покажите журнал действий и различие ролей. Автоматические тесты и ограниченный набор реальных запросов — проверяемые свидетельства, но не промышленная аттестация. Демо-вход открыто переключает роли только в учебном режиме. Доступ для тестирования: http://127.0.0.1:8765 на машине с сервером; для экспертов нужен адрес развернутого экземпляра. Завершите предложением провести пилот с экспертными вопросами и подключением корпоративных систем. Оставшиеся две минуты — вопросы жюри.';
}

for(let i=0;i<p.slides.items.length;i++) {
  const slide=p.slides.items[i];
  const png=await p.export({slide,format:'png',scale:1});
  await fs.writeFile(path.join(work,`slide-${i+1}.png`),new Uint8Array(await png.arrayBuffer()));
}
const candidatePath=path.join(work,'candidate-v2.pptx');
await (await PresentationFile.exportPptx(p)).save(candidatePath);
const {finalizePresentation}=await import(pathToFileURL(path.join(skill,'container_tools/artifact_tool_utils.mjs')).href);
await fs.mkdir(path.join(work,'published'),{recursive:true});
const validatedPath=path.join(work,'published',`validated-${Date.now()}.pptx`);
const result=await finalizePresentation({
  workspaceDir:root,candidatePath,finalPath:validatedPath,
  pythonExecutable:process.env.RUNTIME_PYTHON,
  integrityValidatorPath:path.join(skill,'container_tools/inspect_presentation_package_integrity.py'),
  layoutValidatorPath:path.join(skill,'container_tools/inspect_presentation_layout_geometry.py'),
  layoutArgs:['--expected-slide-size-emu','12192000,6858000','--validate-bullet-geometry','--validate-heading-fit'],
  explicitTotalSlideCount:5,requiredNativeTableOwnerSlides:[],requiredNativeChartOwnerSlides:[],
  fontPolicy:{basis:'design',families:['Arial']},verifyArtifactToolImport:true,
  receiptPath:path.join(work,`validation-${Date.now()}.json`)
});
await fs.copyFile(validatedPath,path.join(output,'Регула — защита.pptx'));
const pdfPath=path.join(output,'Регула — защита.pdf');
let pdfRasterized=false;
try {
  const pdf=await p.export({format:'pdf'});
  await fs.writeFile(pdfPath,new Uint8Array(await pdf.arrayBuffer()));
} catch (error) {
  console.warn('Native PDF export unavailable; using verified high-resolution slide renders:',error.message);
  for(let i=0;i<p.slides.items.length;i++) {
    const png=await p.export({slide:p.slides.items[i],format:'png',scale:2});
    await fs.writeFile(path.join(work,`pdf-slide-${i+1}.png`),new Uint8Array(await png.arrayBuffer()));
  }
  const fallbackPath=path.join(work,'export_pdf.py');
  await fs.writeFile(fallbackPath,`from pathlib import Path\nimport sys\nfrom reportlab.pdfgen import canvas\nfrom reportlab.lib.utils import ImageReader\nfrom pypdf import PdfReader\nwork, target = Path(sys.argv[1]), Path(sys.argv[2])\npdf = canvas.Canvas(str(target), pagesize=(960,540), pageCompression=1)\npdf.setTitle('Регула: защита решения')\npdf.setAuthor('Регула')\npdf.setSubject('PDF-копия слайдов. Редактируемый оригинал: PPTX.')\nfor number in range(1,6):\n    pdf.drawImage(ImageReader(str(work / f'pdf-slide-{number}.png')), 0,0, width=960,height=540)\n    pdf.showPage()\npdf.save()\nassert len(PdfReader(str(target)).pages)==5\n`, 'utf8');
  await promisify(execFile)(process.env.RUNTIME_PYTHON,[fallbackPath,work,pdfPath],{windowsHide:true});
  pdfRasterized=true;
}
await fs.writeFile(path.join(work,'delivery.json'),JSON.stringify({pptx:path.join(output,'Регула — защита.pptx'),pdf:pdfPath,pdfRasterized,slideCount:5},null,2));
console.log(JSON.stringify({...result,pdfPath,pdfRasterized}));
