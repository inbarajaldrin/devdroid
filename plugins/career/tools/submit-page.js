(async () => {
  const careerApproved = __APPROVED__;
  const readForm = __READER__;
  const same = (a,b) => {
    if (a === b) return true;
    if (!a || !b || typeof a !== 'object' || typeof b !== 'object') return false;
    const keys=Object.keys(a).sort(), other=Object.keys(b).sort();
    return keys.length===other.length && keys.every((key,index)=>key===other[index] && same(a[key],b[key]));
  };
  const refuse = (code='FORM_CHANGED') => ({career_submit:false,code});
  const initialControl=document.querySelector(careerApproved.contract.submit_control.selector);
  if (!initialControl) return refuse();
  initialControl.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
  const files=new Map(Array.from(document.querySelectorAll('input[type=file]')).map(el=>[el,el.files?.[0]]));
  const hashed=await readForm(true);
  const page=await readForm(false);
  if (page.url!==careerApproved.url || page.unreadable.length || page.challenge || page.signed_out || page.blocked || page.account_creation || page.confirmation) return refuse();
  const invalid=Array.from(initialControl.form?.querySelectorAll('input:invalid,select:invalid,textarea:invalid') || []).filter(el=>!
    page.fields.some(f=>f.type==='multiselect' && f.value && Object.values(f.option_selectors).some(s=>document.querySelector(s)===el)));
  if (page.errors.length || invalid.length) return refuse('FORM_ERRORS');
  const contract=careerApproved.contract;
  if (!same(page.submit_control,contract.submit_control) || page.heading!==contract.heading || page.submission_terms!==(contract.submission_terms || '')) return refuse();
  if (page.fields.length!==careerApproved.rows.length) return refuse();
  const hashes=new Map(hashed.fields.map(field=>[field.name,field.sha256]));
  const uploadIds=new Map(hashed.fields.map(field=>[field.name,field.upload_id]));
  for (const field of page.fields) {
    const row=careerApproved.rows.find(row=>(row.field_name || row.name)===field.name);
    if (!row || row.missing || row.type!==field.type || row.required!==field.required || !same(row.options,field.options || []) || (row.context || '')!==(field.context || '')) return refuse();
    if (field.human_only && !(row.human_completed && field.type==='checkbox' && (!field.required || field.value==='Yes'))) return refuse();
    const el=document.querySelector(field.selector);
    if (!el || el.disabled) return refuse();
    let value=el.value || '';
    if (field.type==='combobox') value=field.value;
    if (field.type==='select') value=el.value ? (el.selectedOptions[0]?.textContent || '').replace(/\s+/g,' ').trim() : '';
    if (field.type==='radio') value=Object.entries(field.option_selectors).find(([,selector])=>document.querySelector(selector)?.checked)?.[0] || '';
    if (field.type==='multiselect') value=Object.entries(field.option_selectors).filter(([,selector])=>document.querySelector(selector)?.checked).map(([option])=>option).join('; ');
    if (field.type==='checkbox') value=el.checked ? 'Yes' : 'No';
    if (field.type==='file') {
      value=field.value;
      if (field.upload_pending || (field.upload_native!==false && el.files?.[0]!==files.get(el)) ||
          uploadIds.get(field.name)!==field.upload_id || hashes.get(field.name)!==row.upload_sha256) return refuse();
    }
    if (value!==(field.type==='file' ? row.upload_name || '' : row.value)) return refuse();
  }
  const el=document.querySelector(page.submit_control.selector);
  if (el!==initialControl || !page.submit_control.enabled || el.matches(':disabled')) return refuse();
  const rect=el.getBoundingClientRect(), hit=document.elementFromPoint(rect.left+rect.width/2,rect.top+rect.height/2);
  if (hit!==el && !el.contains(hit)) return refuse('SUBMIT_OBSCURED');
  const key='career:submit:'+careerApproved.handle;
  try {
    if (sessionStorage.getItem(key)) return {career_submit:false,code:'ALREADY_ATTEMPTED'};
    const probe=key+':probe:'+careerApproved.nonce;
    sessionStorage.setItem(probe,careerApproved.nonce);
    if (sessionStorage.getItem(probe)!==careerApproved.nonce) return refuse('SUBMIT_STORAGE');
    sessionStorage.removeItem(probe);
    if (careerApproved.dry_run) return {career_preflight:true,nonce:careerApproved.nonce};
    sessionStorage.setItem(key,careerApproved.nonce);
  } catch {return {career_submit:false,code:'SUBMIT_STORAGE'};}
  // One DOM click, with no native-click fallback or retry. Intent is already durable on disk.
  el.click();
  return {career_submit:true,clicked:true,click_method:'guarded-dom',nonce:careerApproved.nonce};
})()
