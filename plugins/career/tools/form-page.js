(async (hashFiles = true, choiceCatalog = {}) => {
  const visible = el => {
    if (!el || !el.getClientRects().length || (el.closest('details:not([open])') && !el.closest('summary'))) return false;
    for (let node=el;node;node=node.parentElement) {
      const style=getComputedStyle(node);
      if (style.visibility==='hidden' || style.display==='none' || Number(style.opacity)===0) return false;
    }
    const rect=el.getBoundingClientRect();
    return rect.right + scrollX > 0 && rect.bottom + scrollY > 0 && rect.left + scrollX < Math.max(document.documentElement.scrollWidth, innerWidth);
  };
  const text = el => (el?.innerText || el?.textContent || '').replace(/\s+/g, ' ').trim();
  const labelText = el => {
    const copy=el.cloneNode(true);
    copy.querySelectorAll('input,select,textarea,button').forEach(node=>node.remove());
    return text(copy);
  };
  const contextFor = el => {
    const group=el.closest('fieldset,[role=group],.field,.form-group');
    if (!group) return '';
    if (group.querySelectorAll('input,select,textarea').length > 1) return text(group.querySelector('legend'));
    const copy=group.cloneNode(true);
    copy.querySelectorAll('p,small,div,span').forEach(node=>{if (/\bby (?:submitting|applying|clicking|selecting|sending|continuing)\b/i.test(text(node)) && !Array.from(node.children).some(child=>/\bby (?:submitting|applying|clicking|sending)\b/i.test(text(child)))) node.remove();});
    return text(copy);
  };
  const precedingContext = el => {
    const parts=[];
    for (let node=el.closest('label') || el;node && node!==formOf(el) && node!==document.body;node=node.parentElement) {
      const previous=node.previousElementSibling;
      if (previous && !previous.matches('input,select,textarea') && !previous.querySelector('input,select,textarea,button')) parts.push(text(previous));
    }
    return parts.join(' ');
  };
  const name = el => el.getAttribute('aria-label') ||
    (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id => text(document.getElementById(id))).join(' ').trim() ||
    Array.from(el.labels || []).map(labelText).join(' ').trim() || el.getAttribute('placeholder') || el.name || text(el);
  const selector = el => {
    if (el.id && document.querySelectorAll('#' + CSS.escape(el.id)).length === 1) return '#' + CSS.escape(el.id);
    const parts = [];
    while (el && el.nodeType === 1) {
      const tag = el.tagName.toLowerCase();
      const peers = el.parentElement ? Array.from(el.parentElement.children).filter(p => p.tagName === el.tagName) : [el];
      parts.unshift(`${tag}:nth-of-type(${peers.indexOf(el) + 1})`); el = el.parentElement;
    }
    return parts.join(' > ');
  };
  const formOf = el => el.form || el.closest('form');
  const forms = Array.from(document.forms).filter(el => visible(el) && !el.closest('footer,nav'));
  const preferred = forms.filter(el => /application|apply|candidate/i.test(el.id + ' ' + el.getAttribute('aria-label')));
  const activeForm = preferred.length === 1 ? preferred[0] : forms.length === 1 ? forms[0] : null;
  const belongs = el => !el.closest('footer,nav') && (!formOf(el) || formOf(el) === activeForm);
  const formKey = el => formOf(el) ? selector(formOf(el)) : null;
  const uploadGroup = el => el.type==='file' && el.closest('[role=group][aria-labelledby]');
  const nativeUpload = el => el.type==='file' && formOf(el)===activeForm && !el.disabled && !el.parentElement.closest('[hidden],[aria-hidden=true]') && (Array.from(el.labels || []).some(visible) || el.getAttribute('aria-label') || uploadGroup(el));
  const uploadKey=Symbol.for('devdroid.career.uploads');
  const uploads=window[uploadKey] ||= {bindings:new WeakMap(),files:new WeakMap(),serial:0};
  if (!uploads.listening) {
    document.addEventListener('change',event=>{
      const input=event.target, group=uploads.bindings.get(input);
      if (group && input.files?.length===1) uploads.files.set(group,{file:input.files[0],id:++uploads.serial});
      else if (group) uploads.files.delete(group);
    },true);
    uploads.listening=true;
  }
  for (const group of (activeForm || document).querySelectorAll('.file-upload[role=group][aria-labelledby]')) {
    const inputs=group.querySelectorAll('input[type=file]');
    if (inputs.length===1 && nativeUpload(inputs[0])) {
      const input=inputs[0];uploads.bindings.set(input,group);
      if (input.files?.length===1 && uploads.files.get(group)?.file!==input.files[0]) uploads.files.set(group,{file:input.files[0],id:++uploads.serial});
      else if (!input.files?.length) uploads.files.delete(group);
    }
  }
  const ownedUpload = el => {
    const group=el.closest('.file-upload[role=group][aria-labelledby]');
    const inputs=Array.from(group?.querySelectorAll('input[type=file]') || []);
    if (inputs.length===1 && nativeUpload(inputs[0])) return {group,input:inputs[0]};
    return group && !inputs.length && group.querySelector('.file-upload__filename,.file-upload__progressbar') ? {group,input:null} : null;
  };
  const uploadMetadata = el => {
    const upload=ownedUpload(el);
    return !!upload && (el===upload.group || (upload.group.getAttribute('aria-labelledby') || '').split(/\s+/).includes(el.id) ||
      Array.from(upload.input?.labels || []).includes(el) || (upload.input && el.tagName==='LABEL' && el.htmlFor===upload.input.id+'_text'));
  };
  const uploadEvidence = async (input,group) => {
    const record=group && uploads.files.get(group), file=input?.files?.[0] || record?.file;
    const caption=group?.querySelector('.file-upload__filename')?.cloneNode(true);
    caption?.querySelectorAll('button,[role=button],svg').forEach(el=>el.remove());
    const value=input ? input.files?.[0]?.name || '' : caption?.textContent.trim() || '';
    const evidence={value,upload_native:!!input,upload_id:record?.id,upload_pending:!!group?.querySelector('.file-upload__progressbar')};
    if (group) {
      const removes=Array.from(group.querySelectorAll('button')).filter(el=>/^remove file$/i.test(el.getAttribute('aria-label') || text(el)));
      if (removes.length===1) evidence.upload_remove_selector=selector(removes[0]);
    }
    if (hashFiles && file && (!input || input.files.length===1) && file.name===value && !evidence.upload_pending) evidence.sha256=
      Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',await file.arrayBuffer()))).map(b=>b.toString(16).padStart(2,'0')).join('');
    return evidence;
  };
  // React Select owns its search input and hidden validity proxy; only the named selection is an answer.
  const selectShell = el => el.matches('input[role=combobox].select__input') && el.id &&
    el.closest('.select-shell')?.querySelectorAll('input[role=combobox].select__input').length===1 &&
    el.closest('.select-shell')?.querySelector('.select__control') ? el.closest('.select-shell') : null;
  const selectInput = el => el.closest('.select-shell')?.querySelector('input[role=combobox].select__input');
  const captchaResponse = el => el.tagName==='TEXTAREA' && /^(?:g-recaptcha-response(?:-\d+)?|h-captcha-response)$/.test(el.name || el.id);
  const widgetHelper = el => {
    const upload=ownedUpload(el);
    if (upload?.input && el.tagName==='TEXTAREA' && el.id===upload.input.id+'_text' && !visible(el) &&
        Array.from(upload.group.querySelectorAll('label')).some(label=>label.htmlFor===el.id)) return true;
    const primary=selectInput(el);
    if (primary && primary!==el && selectShell(primary) && el.parentElement===selectShell(primary) && el.tagName==='INPUT' && el.required && el.getAttribute('aria-hidden')==='true' && el.tabIndex===-1 && !visible(el) && !name(el)) return true;
    return el.matches('input.iti__search-input') && !visible(el) &&
      !!el.closest('.iti')?.querySelector('input.iti__tel-input') &&
      !!el.closest('.phone-input')?.querySelector('.phone-input__country input[role=combobox].select__input');
  };
  const choiceVisible = el => visible(el) || Array.from(el.labels || []).some(visible);
  const checkboxGroup = el => {
    if (el.type !== 'checkbox' || !el.name) return null;
    const group = Array.from(document.querySelectorAll('input[type=checkbox]')).filter(r => r.name === el.name && r.form === el.form && !r.disabled && choiceVisible(r));
    if (group.length < 2) return null;
    let container = el.parentElement;
    while (container && !group.every(r=>container.contains(r))) container = container.parentElement;
    const label = Array.from(document.querySelectorAll('label')).find(l =>
      [el.name,el.name.replace(/\[\]$/, '')].includes(l.htmlFor) && !group.some(r=>Array.from(r.labels || []).includes(l))) ||
      container?.querySelector('legend,[role=heading],.field-label');
    if (!label || !text(label)) return null;
    const options = group.map(name);
    if (options.some(o=>!o) || new Set(options).size !== options.length) return null;
    return {group,label,container,options};
  };
  const unreadable = [];
  for (const el of document.querySelectorAll('input[type=file], [role=combobox], [role=listbox], [role=checkbox], [role=switch], [role=textbox], [role=searchbox], [role=radio], [role=slider], [role=spinbutton], [contenteditable], [aria-required=true]')) {
    if (!belongs(el) || uploadMetadata(el) || widgetHelper(el) || captchaResponse(el) || (el.hasAttribute('contenteditable') && !el.isContentEditable)) continue;
    const ownedMenu=el.getAttribute('role')==='listbox' && el.closest('.select-shell')?.querySelector('input[role=combobox].select__input');
    if ((el.type === 'file' && (el.disabled || (!visible(el) && !nativeUpload(el))) && !(el.files?.length === 1 && el.parentElement.closest('[hidden],[aria-hidden=true]'))) || (visible(el) && el.getAttribute('role') === 'combobox' && el.tagName !== 'SELECT' && !selectShell(el)) || (visible(el) && !ownedMenu && !el.matches('input,select,textarea') && !el.querySelector('input,select,textarea'))) unreadable.push(name(el) || 'Unnamed widget');
  }
  for (const el of document.querySelectorAll('input,select,textarea')) {
    if (!belongs(el) || widgetHelper(el) || captchaResponse(el) || ['hidden','submit','button','reset','image','file'].includes(el.type) || visible(el) || (selectShell(el) && visible(selectShell(el).querySelector('.select__control'))) || (checkboxGroup(el) && choiceVisible(el))) continue;
    const inactive=el.parentElement.closest('[hidden],[aria-hidden=true]');
    if (!inactive || (el.type==='checkbox' && el.checked) || el.closest('details:not([open])')) unreadable.push('Unread native field: '+name(el));
  }
  for (const el of (activeForm || document.body).querySelectorAll('*')) {
    if (el.shadowRoot?.querySelector('input,select,textarea,[contenteditable],button') || el.tagName.includes('-')) unreadable.push('Custom or shadow field requires an adapter');
    if (el.matches('button,[role=button]') && visible(el) && (/^(?:yes|no)$/i.test(text(el)) || el.hasAttribute('aria-pressed')) && !el.parentElement.querySelector('input,select,textarea')) unreadable.push('Unread button question: '+text(el.parentElement));
  }
  for (const el of document.querySelectorAll('iframe')) {
    if (visible(el) && !el.closest('footer') && !/recaptcha|hcaptcha|turnstile|challenge-platform/.test(el.src)) unreadable.push('Embedded application frame requires an adapter');
  }
  const fields = [], radios = new Set(), checkboxGroups = new Set();
  for (const el of document.querySelectorAll('input, select, textarea')) {
    if (!belongs(el) || widgetHelper(el) || captchaResponse(el) || (!visible(el) && !nativeUpload(el) && !(selectShell(el) && visible(selectShell(el).querySelector('.select__control'))) && !(checkboxGroup(el) && choiceVisible(el))) || ['hidden','submit','button','reset','image'].includes(el.type)) continue;
    const type = el.tagName === 'TEXTAREA' ? 'textarea' : el.tagName === 'SELECT' ? 'select' : el.type || 'text';
    const shell=selectShell(el);
    if (shell) {
      const list=document.getElementById(el.getAttribute('aria-controls') || 'react-select-'+el.id+'-listbox');
      if (shell.querySelector('.select__value-container--is-multi,.select__multi-value') || list?.getAttribute('aria-multiselectable')==='true') unreadable.push('Multiple-selection dropdown requires an adapter: '+name(el));
      const label=name(el), key=selector(el), selected=shell.querySelector('.select__single-value');
      const countryKey=node=>Array.from(node?.querySelector('.iti__flag')?.classList || []).find(c=>/^iti__[a-z]{2}$/.test(c)) || '';
      const live=Array.from(list?.querySelectorAll('[role=option]') || []).filter(o=>o.getAttribute('aria-disabled')!=='true').map(o=>({label:text(o),selector:selector(o),key:countryKey(o)}));
      const cached=choiceCatalog[key];
      const catalog=cached?.label===label ? cached.options : live;
      const selectedKey=countryKey(selected);
      const value=selectedKey ? catalog.find(o=>o.key===selectedKey)?.label || text(selected) : text(selected);
      const phoneCountry=!!el.closest('.phone-input__country');
      fields.push({name:phoneCountry?'Phone country':label,visible_name:label,selector:key,form:formKey(el),type:'combobox',required:el.required || el.getAttribute('aria-required')==='true',
        options:catalog.map(o=>o.label),value,context:label,human_only:false,
        choice:{label,open_selector:selector(shell.querySelector('.select__control')),clear_selector:shell.querySelector('.select__clear-indicator') ? selector(shell.querySelector('.select__clear-indicator')) : null,
          expanded:el.getAttribute('aria-expanded')==='true',search:el.value,live,loading:!!shell.querySelector('.select__loading-indicator,[aria-busy=true]') || !!list?.querySelector('.select__menu-notice--loading,[aria-busy=true]'),
          ready:!!list && !list.querySelector('.select__menu-notice--loading,[aria-busy=true]'),inspected:cached?.label===label,
          selected_key:selectedKey,phone_country:phoneCountry}});
      continue;
    }
    const multi = checkboxGroup(el);
    if (multi) {
      const key = formKey(el)+':'+el.name;
      if (checkboxGroups.has(key)) continue;
      checkboxGroups.add(key);
      fields.push({name:text(multi.label), visible_name:text(multi.label), form:formKey(el), type:'multiselect',
        required:multi.group.some(r=>r.required) || multi.container?.getAttribute('aria-required') === 'true' || /\*/.test(text(multi.label)),
        value:multi.group.filter(r=>r.checked).map(name).join('; '), options:multi.options,
        option_selectors:Object.fromEntries(multi.group.map(r=>[name(r),selector(r)])), selector:selector(el),
        context:text(multi.label), human_only:false});
      continue;
    }
    if (type === 'radio') {
      const key = el.form?.id + ':' + el.name;
      if (radios.has(key)) continue;
      radios.add(key);
      const group = Array.from(document.querySelectorAll('input[type=radio]')).filter(r => r.name === el.name && r.form === el.form && visible(r));
      const legend = el.closest('fieldset')?.querySelector('legend');
      const groupName = el.closest('[role=radiogroup]');
      fields.push({name: text(legend) || (groupName && name(groupName)) || el.name, form: formKey(el), type, required: group.some(r => r.required),
        value: name(group.find(r => r.checked) || el) && (group.some(r => r.checked) ? name(group.find(r => r.checked)) : ''),
        options: group.map(name), option_selectors: Object.fromEntries(group.map(r => [name(r), selector(r)])), selector: selector(el)});
      continue;
    }
    const field = {name: name(el), form: formKey(el), visible_name: Array.from(el.labels || []).map(labelText).join(' '), context: [contextFor(el), precedingContext(el)].join(' '), type, required: !!el.required, human_only: type === 'password' || type === 'checkbox' || /password|one-time-code/.test(el.autocomplete || ''), value: type === 'password' || /password|one-time-code/.test(el.autocomplete || '') ? '' : el.value || '', selector: selector(el)};
    field.required ||= el.getAttribute('aria-required')==='true';
    if (uploadGroup(el)) {
      field.name=name(uploadGroup(el)); field.visible_name=field.name;field.context=field.name;
      field.required ||= uploadGroup(el).getAttribute('aria-required')==='true';
    }
    if (type === 'checkbox') field.value = el.checked ? 'Yes' : 'No';
    if (type === 'select') { field.options = Array.from(el.options).map(text); field.value = el.value ? text(el.selectedOptions[0]) : ''; }
    if (type === 'file') {
      Object.assign(field,await uploadEvidence(el,uploadGroup(el)));
    }
    fields.push(field);
  }
  for (const group of (activeForm || document).querySelectorAll('.file-upload[role=group][aria-labelledby]')) {
    if (!visible(group) || group.querySelector('input[type=file]') || !ownedUpload(group)) continue;
    fields.push({name:name(group),visible_name:name(group),context:name(group),form:formKey(group),selector:selector(group),type:'file',
      required:group.getAttribute('aria-required')==='true',human_only:false,...await uploadEvidence(null,group)});
  }
  fields.sort((a,b)=>{
    const left=document.querySelector(a.selector),right=document.querySelector(b.selector);
    return left===right?0:left.compareDocumentPosition(right)&Node.DOCUMENT_POSITION_FOLLOWING?-1:1;
  });
  const sectionFor = el => {
    const education=el.closest('.education--form');
    if (education) return 'Education '+(Array.from((activeForm || document).querySelectorAll('.education--form')).indexOf(education)+1);
    if (el.closest('.phone-input')) return '';
    const legend=el.closest('fieldset')?.querySelector('legend');
    if (legend) return text(legend);
    const headings=Array.from((activeForm || document).querySelectorAll('h2,h3,h4,h5,[role=heading]')).filter(h=>visible(h) && (h.compareDocumentPosition(el)&Node.DOCUMENT_POSITION_FOLLOWING));
    return text(headings.at(-1));
  };
  const applicantSection = section => /^(?:contact(?: information| details)?|personal (?:information|details)|your (?:details|information|contact details)|about you|applicant (?:information|details)|profile|application(?: questions)?|basic information|additional (?:questions|information)|replies|review|questions)$/i.test(section);
  for (const field of fields) {
    const el=document.querySelector(field.selector);
    field.section=sectionFor(el);
    const education=el.closest('.education--form');
    if (education && /^(school|degree|discipline)--/.test(el.id)) field.education={index:Array.from((activeForm || document).querySelectorAll('.education--form')).indexOf(education),key:el.id.split('--')[0]};
    const visibleLabel=field.visible_name || '';
    const accessible=el.getAttribute('aria-label');
    const normalized=s=>s.toLowerCase().replace(/[\s*]+/g,' ').trim();
    if (field.type !== 'multiselect' && accessible && visibleLabel && normalized(accessible)!==normalized(visibleLabel)) unreadable.push('Visible and accessible labels disagree: '+visibleLabel);
  }
  const repeatedNames=new Set(fields.filter(field=>fields.filter(other=>other.name===field.name).length>1).map(field=>field.name));
  for (const field of fields) {
    if (repeatedNames.has(field.name) && field.section) field.name=field.section+' / '+field.name;
    else if (field.section && !applicantSection(field.section) && !['radio','multiselect'].includes(field.type)) field.name=field.section+' / '+field.name;
  }
  const visibleControlName = el => el.tagName==='INPUT' ? el.value || '' : text(el);
  const accessibleControlName = el => el.getAttribute('aria-label') ||
    (el.getAttribute('aria-labelledby') || '').split(/\s+/).map(id=>text(document.getElementById(id))).join(' ').trim() || visibleControlName(el);
  const controls = Array.from(document.querySelectorAll('button, input[type=submit], a[href]')).filter(el => visible(el) && belongs(el)).map(el => ({
    visible_name:visibleControlName(el), accessible_name:accessibleControlName(el), form:formKey(el), name:visibleControlName(el),
    enabled:!el.matches(':disabled') && el.getAttribute('aria-disabled')!=='true', selector:selector(el),
    blocking:!!el.closest('dialog,[aria-modal=true],[role=alertdialog]'), tag:el.tagName.toLowerCase(), in_form:!!formOf(el), href:el.href || null,
  }));
  const pick = pattern => { const matches = controls.filter(c => pattern.test(c.name)); return matches.length === 1 ? matches[0] : null; };
  const nextMatches=controls.filter(c=>/^next$/i.test(c.visible_name) && /^next$/i.test(c.accessible_name));
  const next=nextMatches.length===1 ? nextMatches[0] : null;
  for (const control of controls) {
    if (control.visible_name && control.accessible_name.toLowerCase()!==control.visible_name.toLowerCase()) unreadable.push('Visible and accessible control names disagree');
  }
  const previous = pick(/^(?:back|previous|go back)$/i);
  const apply = controls.filter(c => !c.in_form && c.tag === 'a' && /^(?:apply|apply now|apply for this job)$/i.test(c.name));
  const submit = controls.filter(c => /^(?:submit|submit application|send (?:my )?application)$/i.test(c.name) ||
    (c.in_form && /^(?:apply|apply now)$/i.test(c.name)));
  const body = document.body?.innerText || '';
  const heading = ['[aria-current=step]', 'h2', 'legend', 'h1'].map(query => Array.from(document.querySelectorAll(query)).find(visible)).find(Boolean);
  const label = text(heading) || document.title || 'Application';
  const agreement = value => /\bby\s+(?:submitting|applying|clicking|selecting|sending|continuing)\b/i.test(value) && /\b(?:agree|accept|consent|acknowledge|authori[sz]e|certif(?:y|ication)|terms|privacy|arbitration|policy)\b/i.test(value);
  const submissionTerms = Array.from(document.querySelectorAll('p,small,div,span,label,section')).filter(el=>visible(el) && !el.closest('footer,nav') && agreement(text(el)) && !Array.from(el.children).some(child=>agreement(text(child)))).map(text).filter((value,index,array)=>array.indexOf(value)===index).join('\n');
  const errors = Array.from(document.querySelectorAll('[role=alert], .field-error, [aria-invalid=true]')).filter(visible).filter(el=>el.matches('.field-error,[aria-invalid=true]') || /\b(?:error|invalid|required|missing|failed|please (?:fix|answer|complete)|could not|cannot submit)\b/i.test(text(el))).map(text).filter(Boolean);
  const successPattern=/^(?:your )?(?:application (?:(?:has been|was) )?(?:successfully )?(?:submitted|received)|(?:thank you|thanks) for applying|submission received)[.!\s]/i;
  const successText=Array.from(document.querySelectorAll('h1,h2,[role=status],[role=alert]')).filter(visible).map(text).find(t=>successPattern.test(t+' '));
  const confirmation = !fields.length && !submit.length && !errors.length && successText ? successText : null;
  return {url: location.href, text: body, fields, controls, errors, confirmation, blocked:Array.from(document.querySelectorAll('dialog,[aria-modal=true]')).some(visible), heading:[text(Array.from(document.querySelectorAll('h1')).find(visible)),label].filter((value,index,array)=>value && array.indexOf(value)===index).join(' / '), submission_terms:submissionTerms,
    unreadable: forms.length > 1 && !activeForm ? unreadable.concat('Choose the application form explicitly') : unreadable,
    account_creation: Array.from(document.querySelectorAll('h1,h2,form legend')).some(el => !el.closest('footer,nav') && visible(el) && /create (?:your |an? )?(?:candidate |careers? )?(?:account|profile)|join .+ careers|\bsign\s*up\b|\bregister(?: to apply)?\b/i.test(text(el))),
    challenge: /verify (?:that )?you are human|security challenge|solve (?:the |this )?captcha/i.test(body) || Array.from(document.querySelectorAll('iframe')).some(el => {
      const url=new URL(el.src,location.href);
      return visible(el) && /^https?:$/.test(url.protocol) && /recaptcha|hcaptcha|turnstile|challenge-platform/i.test(url.hostname+url.pathname) && url.searchParams.get('size')!=='invisible';
    }),
    signed_out: fields.some(field => field.type === 'password'),
    step: {id: location.pathname + location.search + '#' + label, label}, next_control: next,
    previous_control: previous, apply_control: apply.length === 1 ? apply[0] : null,
    submit_control: submit.length === 1 ? submit[0] : null};
})()
