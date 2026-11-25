import{$ as e,C as t,E as n,F as r,G as i,M as a,N as o,O as s,P as c,Q as l,S as u,T as d,V as f,Z as p,_ as m,b as h,d as g,g as _,i as v,j as y,l as b,n as x,p as S,q as C,r as w,t as T,u as E,ut as D,x as O,y as k}from"./shared.js";import{I as A,L as j,r as M}from"./shared2.js";import{i as N}from"./shared3.js";var P=g.extend({name:`toggleswitch`,style:`
    .p-toggleswitch {
        display: inline-block;
        width: dt('toggleswitch.width');
        height: dt('toggleswitch.height');
    }

    .p-toggleswitch-input {
        cursor: pointer;
        appearance: none;
        position: absolute;
        top: 0;
        inset-inline-start: 0;
        width: 100%;
        height: 100%;
        padding: 0;
        margin: 0;
        opacity: 0;
        z-index: 1;
        outline: 0 none;
        border-radius: dt('toggleswitch.border.radius');
    }

    .p-toggleswitch-slider {
        cursor: pointer;
        width: 100%;
        height: 100%;
        border-width: dt('toggleswitch.border.width');
        border-style: solid;
        border-color: dt('toggleswitch.border.color');
        background: dt('toggleswitch.background');
        transition:
            background dt('toggleswitch.transition.duration'),
            color dt('toggleswitch.transition.duration'),
            border-color dt('toggleswitch.transition.duration'),
            outline-color dt('toggleswitch.transition.duration'),
            box-shadow dt('toggleswitch.transition.duration');
        border-radius: dt('toggleswitch.border.radius');
        outline-color: transparent;
        box-shadow: dt('toggleswitch.shadow');
    }

    .p-toggleswitch-handle {
        position: absolute;
        top: 50%;
        display: flex;
        justify-content: center;
        align-items: center;
        background: dt('toggleswitch.handle.background');
        color: dt('toggleswitch.handle.color');
        width: dt('toggleswitch.handle.size');
        height: dt('toggleswitch.handle.size');
        inset-inline-start: dt('toggleswitch.gap');
        margin-block-start: calc(-1 * calc(dt('toggleswitch.handle.size') / 2));
        border-radius: dt('toggleswitch.handle.border.radius');
        transition:
            background dt('toggleswitch.transition.duration'),
            color dt('toggleswitch.transition.duration'),
            inset-inline-start dt('toggleswitch.slide.duration'),
            box-shadow dt('toggleswitch.slide.duration');
    }

    .p-toggleswitch.p-toggleswitch-checked .p-toggleswitch-slider {
        background: dt('toggleswitch.checked.background');
        border-color: dt('toggleswitch.checked.border.color');
    }

    .p-toggleswitch.p-toggleswitch-checked .p-toggleswitch-handle {
        background: dt('toggleswitch.handle.checked.background');
        color: dt('toggleswitch.handle.checked.color');
        inset-inline-start: calc(dt('toggleswitch.width') - calc(dt('toggleswitch.handle.size') + dt('toggleswitch.gap')));
    }

    .p-toggleswitch:not(.p-disabled):has(.p-toggleswitch-input:hover) .p-toggleswitch-slider {
        background: dt('toggleswitch.hover.background');
        border-color: dt('toggleswitch.hover.border.color');
    }

    .p-toggleswitch:not(.p-disabled):has(.p-toggleswitch-input:hover) .p-toggleswitch-handle {
        background: dt('toggleswitch.handle.hover.background');
        color: dt('toggleswitch.handle.hover.color');
    }

    .p-toggleswitch:not(.p-disabled):has(.p-toggleswitch-input:hover).p-toggleswitch-checked .p-toggleswitch-slider {
        background: dt('toggleswitch.checked.hover.background');
        border-color: dt('toggleswitch.checked.hover.border.color');
    }

    .p-toggleswitch:not(.p-disabled):has(.p-toggleswitch-input:hover).p-toggleswitch-checked .p-toggleswitch-handle {
        background: dt('toggleswitch.handle.checked.hover.background');
        color: dt('toggleswitch.handle.checked.hover.color');
    }

    .p-toggleswitch:not(.p-disabled):has(.p-toggleswitch-input:focus-visible) .p-toggleswitch-slider {
        box-shadow: dt('toggleswitch.focus.ring.shadow');
        outline: dt('toggleswitch.focus.ring.width') dt('toggleswitch.focus.ring.style') dt('toggleswitch.focus.ring.color');
        outline-offset: dt('toggleswitch.focus.ring.offset');
    }

    .p-toggleswitch.p-invalid > .p-toggleswitch-slider {
        border-color: dt('toggleswitch.invalid.border.color');
    }

    .p-toggleswitch.p-disabled {
        opacity: 1;
    }

    .p-toggleswitch.p-disabled .p-toggleswitch-slider {
        background: dt('toggleswitch.disabled.background');
    }

    .p-toggleswitch.p-disabled .p-toggleswitch-handle {
        background: dt('toggleswitch.handle.disabled.background');
    }
`,classes:{root:function(e){var t=e.instance,n=e.props;return[`p-toggleswitch p-component`,{"p-toggleswitch-checked":t.checked,"p-disabled":n.disabled,"p-invalid":t.$invalid}]},input:`p-toggleswitch-input`,slider:`p-toggleswitch-slider`,handle:`p-toggleswitch-handle`},inlineStyles:{root:{position:`relative`}}}),F={name:`ToggleSwitch`,extends:{name:`BaseToggleSwitch`,extends:w,props:{trueValue:{type:null,default:!0},falseValue:{type:null,default:!1},readonly:{type:Boolean,default:!1},tabindex:{type:Number,default:null},inputId:{type:String,default:null},inputClass:{type:[String,Object],default:null},inputStyle:{type:Object,default:null},ariaLabelledby:{type:String,default:null},ariaLabel:{type:String,default:null}},style:P,provide:function(){return{$pcToggleSwitch:this,$parentInstance:this}}},inheritAttrs:!1,emits:[`change`,`focus`,`blur`],methods:{getPTOptions:function(e){return(e===`root`?this.ptmi:this.ptm)(e,{context:{checked:this.checked,disabled:this.disabled}})},onChange:function(e){if(!this.disabled&&!this.readonly){var t=this.checked?this.falseValue:this.trueValue;this.writeValue(t,e),this.$emit(`change`,e)}},onFocus:function(e){this.$emit(`focus`,e)},onBlur:function(e){var t,n;this.$emit(`blur`,e),(t=(n=this.formField).onBlur)==null||t.call(n,e)}},computed:{checked:function(){return this.d_value===this.trueValue},dataP:function(){return D({checked:this.checked,disabled:this.disabled,invalid:this.$invalid})}}},I=[`data-p-checked`,`data-p-disabled`,`data-p`],L=[`id`,`checked`,`tabindex`,`disabled`,`readonly`,`aria-checked`,`aria-labelledby`,`aria-label`,`aria-invalid`],R=[`data-p`],z=[`data-p`];function B(e,n,i,a,c,l){return o(),t(`div`,s({class:e.cx(`root`),style:e.sx(`root`)},l.getPTOptions(`root`),{"data-p-checked":l.checked,"data-p-disabled":e.disabled,"data-p":l.dataP}),[h(`input`,s({id:e.inputId,type:`checkbox`,role:`switch`,class:[e.cx(`input`),e.inputClass],style:e.inputStyle,checked:l.checked,tabindex:e.tabindex,disabled:e.disabled,readonly:e.readonly,"aria-checked":l.checked,"aria-labelledby":e.ariaLabelledby,"aria-label":e.ariaLabel,"aria-invalid":e.invalid||void 0,onFocus:n[0]||=function(){return l.onFocus&&l.onFocus.apply(l,arguments)},onBlur:n[1]||=function(){return l.onBlur&&l.onBlur.apply(l,arguments)},onChange:n[2]||=function(){return l.onChange&&l.onChange.apply(l,arguments)}},l.getPTOptions(`input`)),null,16,L),h(`div`,s({class:e.cx(`slider`)},l.getPTOptions(`slider`),{"data-p":l.dataP}),[h(`div`,s({class:e.cx(`handle`)},l.getPTOptions(`handle`),{"data-p":l.dataP}),[r(e.$slots,`handle`,{checked:l.checked})],16,z)],16,R)],16,I)}F.render=B;var V=`hiddenFileSettings`,H=[`*.stories.*`,`*_spec.rb`,`*.spec.ts`];function U(e){if(typeof e!=`object`||!e)return{enabled:!1,patterns:[...H]};let t=`enabled`in e&&e.enabled===!0,n=`patterns`in e?e.patterns:null;return{enabled:t,patterns:Array.isArray(n)&&n.every(e=>typeof e==`string`)?n:[...H]}}async function ee(){return U((await chrome.storage.sync.get(V))[V])}async function te(e,t){let n=t?{...e,source:t}:e;await chrome.storage.sync.set({[V]:n})}function W(e,t){function n(n,r){if(r===`sync`&&`hiddenFileSettings`in n){let r=n[V]?.newValue;G(r,t)||e(U(r))}}return chrome.storage.onChanged.addListener(n),()=>chrome.storage.onChanged.removeListener(n)}function G(e,t){return t!==void 0&&typeof e==`object`&&!!e&&`source`in e&&e.source===t}function K(e,t){let n=C(``),r=C(null),i=k(()=>r.value?j(r.value):``),a=k(()=>r.value!==null);function o(){r.value=null}function s(){let i=n.value.trim();r.value=q(i,e.value),!r.value&&(t(i),n.value=``)}return f(n,o),{pattern:n,feedback:i,invalid:a,handleAdd:s}}function q(e,t){return e?t.includes(e)?`filePatternDuplicate`:null:`filePatternEmpty`}function J(){let e=C({enabled:!1,patterns:[]}),t=C(!1),n=C(!1),r=k(()=>!t.value),i=crypto.randomUUID(),a=X(e,n,i);function o(t){a({...e.value,enabled:t})}function s(t){a({...e.value,patterns:[...e.value.patterns,t]})}function c(t){a({...e.value,patterns:e.value.patterns.filter(e=>e!==t)})}Y({settings:e,ready:t,failed:n,source:i});let l=k(()=>e.value.enabled),u=k(()=>e.value.patterns);return{enabled:l,patterns:u,...K(u,s),disabled:r,failed:n,handleToggle:o,remove:c}}function Y(e){let t;y(async()=>{try{e.settings.value=await ee(),t=W(t=>{e.settings.value=t},e.source),e.ready.value=!0}catch{e.failed.value=!0}}),a(()=>t?.())}function X(e,t,n){let r=Promise.resolve();return i=>{e.value=i,t.value=!1,r=r.then(()=>te(i,n)).catch(()=>{t.value=!0})}}function Z(e,t){y(async()=>{let n=await t({type:`repositorySettings`});M(n)?e.feedback.value=n.error:(e.settings.value=n,e.busy.value=!1)}),f(e.repository,()=>{e.feedback.value=``})}function Q(e,t){return async(n,r)=>{e.busy.value=!0;try{let i=await t({type:r?`removeRepository`:`addRepository`,repo:n});if(M(i)){e.feedback.value=i.error;return}e.feedback.value=``,e.settings.value=i,r||(e.repository.value=``)}finally{e.busy.value=!1}}}function ne(e,t){return N(e)?[...t.configured,...t.added].includes(e)?j(`repositoryDuplicate`):``:j(`repositoryInvalid`)}function re(e,t){function n(){if(e.busy.value)return;let n=e.repository.value.trim().toLowerCase();e.feedback.value=ne(n,e.settings.value),e.feedback.value||t(n,!1)}function r(n){e.busy.value||t(n,!0)}return{handleAdd:n,remove:r}}function ie(e=b().ask){let t=C({configured:[],added:[]}),n=C(``),r=C(``),i=C(!0),a={settings:t,repository:n,feedback:r,busy:i},o=k(()=>Object.fromEntries([...t.value.configured.map(e=>({repo:e,configured:!0})),...t.value.added.filter(e=>!t.value.configured.includes(e)).map(e=>({repo:e,configured:!1}))].map(e=>[e.repo,e])));return Z(a,e),{entries:o,repository:n,feedback:r,busy:i,...re(a,Q(a,e))}}var ae={class:`settings-pattern`},oe={key:0,class:`settings-help`},se=n({__name:`RepositoryItem`,props:{repo:{},configured:{type:Boolean},disabled:{type:Boolean}},emits:[`remove`],setup(n,{emit:r}){let i=n,a=r,s=j(`repositoryFromConfig`),c=k(()=>j(`removeRepository`,{repo:i.repo}));function l(){a(`remove`,i.repo)}return(r,i)=>(o(),t(`li`,ae,[h(`code`,null,e(n.repo),1),n.configured?(o(),t(`span`,oe,e(p(s)),1)):(o(),O(p(v),{key:1,type:`button`,icon:`pi pi-trash`,severity:`secondary`,size:`small`,text:``,rounded:``,"aria-label":c.value,disabled:n.disabled,onClick:l},null,8,[`aria-label`,`disabled`]))]))}}),ce={class:`settings-card`,"aria-labelledby":`repositories-title`},le={id:`repositories-title`},ue={class:`settings-help`},de=[`disabled`],fe=[`aria-label`],pe={for:`repository`},me={class:`settings-add-row flex gap-2`},he={id:`repository-feedback`,class:`settings-feedback`,"aria-live":`polite`},ge=n({__name:`RepositoriesSection`,setup(n){let{entries:r,repository:a,feedback:l,busy:u,handleAdd:f,remove:g}=ie(),y=k(()=>!!l.value);function b(e){g(e)}let S=j(`repositoriesTitle`),C=j(`repositoriesHelp`),w=j(`repositoryLabel`),T=j(`repositoryPlaceholder`),E=j(`addRepository`);return(n,g)=>(o(),t(`section`,ce,[h(`h2`,le,e(p(S)),1),h(`p`,ue,e(p(C)),1),h(`fieldset`,{disabled:p(u)},[h(`ul`,{"aria-label":p(S),class:`settings-repositories`},[(o(!0),t(m,null,c(p(r),(e,t)=>(o(),O(se,s({key:t},{ref_for:!0},e,{disabled:p(u),onRemove:b}),null,16,[`disabled`]))),128))],8,fe),h(`form`,{class:`settings-add-form`,onSubmit:g[1]||=_((...e)=>p(f)&&p(f)(...e),[`prevent`])},[h(`label`,pe,e(p(w)),1),h(`div`,me,[d(p(x),{id:`repository`,modelValue:p(a),"onUpdate:modelValue":g[0]||=e=>i(a)?a.value=e:null,placeholder:p(T),disabled:p(u),invalid:y.value,"aria-describedby":`repository-feedback`},null,8,[`modelValue`,`placeholder`,`disabled`,`invalid`]),d(p(v),{type:`submit`,size:`small`,icon:`pi pi-plus`,label:p(E),disabled:p(u)},null,8,[`label`,`disabled`])]),h(`p`,he,e(p(l)),1)],32)],8,de)]))}}),_e={class:`settings-pattern`},ve=n({__name:`PatternItem`,props:{pattern:{},disabled:{type:Boolean}},emits:[`remove`],setup(n,{emit:r}){let i=n,a=r,s=k(()=>j(`removePattern`,{pattern:i.pattern}));function c(){a(`remove`,i.pattern)}return(r,i)=>(o(),t(`li`,_e,[h(`code`,null,e(n.pattern),1),d(p(v),{type:`button`,icon:`pi pi-trash`,severity:`secondary`,size:`small`,text:``,rounded:``,"aria-label":s.value,disabled:n.disabled,onClick:c},null,8,[`aria-label`,`disabled`])]))}}),ye={id:`file-settings`},be={class:`settings-header flex items-center gap-3`},xe=[`alt`],Se={class:`settings-card`,"aria-labelledby":`hide-files-title`},Ce={id:`hide-files-title`},we={class:`settings-help`},Te=[`disabled`],Ee={class:`settings-toggle flex items-center justify-between gap-3`},De={for:`hide-matching-files`},$={id:`patterns-label`},Oe={"aria-labelledby":`patterns-label`},ke={for:`file-pattern`},Ae={class:`settings-add-row flex gap-2`},je={id:`pattern-feedback`,class:`settings-feedback`,"aria-live":`polite`},Me={key:0,class:`settings-error`,role:`alert`},Ne=`./icons/icon-48.png`,Pe=n({__name:`App`,setup(n){let{enabled:r,patterns:a,pattern:s,disabled:f,failed:g,feedback:y,invalid:b,handleToggle:S,handleAdd:C,remove:w}=J(),T=j(`settingsPageTitle`),E=j(`hideFilesTitle`),D=j(`hideFilesEnabled`),A=j(`hideFilesHelp`),M=j(`filePatternsLabel`),N=j(`filePattern`),P=j(`filePatternPlaceholder`),I=j(`addPattern`),L=j(`fileSettingsError`),R=j(`extName`),z=k(()=>({"patterns-inactive":!r.value}));function B(e){w(e)}return(n,w)=>(o(),t(`main`,ye,[h(`header`,be,[h(`img`,{src:Ne,alt:p(R),class:`settings-icon`},null,8,xe),h(`h1`,null,e(p(T)),1)]),h(`section`,Se,[h(`h2`,Ce,e(p(E)),1),h(`p`,we,e(p(A)),1),h(`fieldset`,{disabled:p(f)},[h(`div`,Ee,[h(`label`,De,e(p(D)),1),d(p(F),{"input-id":`hide-matching-files`,"model-value":p(r),disabled:p(f),"onUpdate:modelValue":p(S)},null,8,[`model-value`,`disabled`,`onUpdate:modelValue`])]),h(`div`,{class:l([`settings-patterns`,z.value])},[h(`h3`,$,e(p(M)),1),h(`ul`,Oe,[(o(!0),t(m,null,c(p(a),e=>(o(),O(ve,{key:e,pattern:e,disabled:p(f),onRemove:B},null,8,[`pattern`,`disabled`]))),128))])],2),h(`form`,{onSubmit:w[1]||=_((...e)=>p(C)&&p(C)(...e),[`prevent`]),class:`settings-add-form`},[h(`label`,ke,e(p(N)),1),h(`div`,Ae,[d(p(x),{id:`file-pattern`,modelValue:p(s),"onUpdate:modelValue":w[0]||=e=>i(s)?s.value=e:null,placeholder:p(P),disabled:p(f),invalid:p(b),"aria-describedby":`pattern-feedback`},null,8,[`modelValue`,`placeholder`,`disabled`,`invalid`]),d(p(v),{type:`submit`,size:`small`,icon:`pi pi-plus`,label:p(I),disabled:p(f)},null,8,[`label`,`disabled`])]),h(`p`,je,e(p(y)),1)],32)],8,Te),p(g)?(o(),t(`p`,Me,e(p(L)),1)):u(``,!0)]),d(ge)]))}});document.title=j(`settingsPageTitle`),document.documentElement.lang=A(),S(Pe).use(E,T).mount(`#app`);