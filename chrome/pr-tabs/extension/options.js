import{A as e,B as t,D as n,K as r,M as i,N as a,P as o,Q as s,S as c,T as l,W as u,X as d,Z as f,b as p,f as m,g as h,h as g,i as _,j as v,l as y,lt as b,n as x,r as S,t as C,u as w,v as T,w as E,x as D,y as O}from"./shared.js";import{n as k,t as A}from"./shared2.js";var j=w.extend({name:`toggleswitch`,style:`
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
`,classes:{root:function(e){var t=e.instance,n=e.props;return[`p-toggleswitch p-component`,{"p-toggleswitch-checked":t.checked,"p-disabled":n.disabled,"p-invalid":t.$invalid}]},input:`p-toggleswitch-input`,slider:`p-toggleswitch-slider`,handle:`p-toggleswitch-handle`},inlineStyles:{root:{position:`relative`}}}),M={name:`ToggleSwitch`,extends:{name:`BaseToggleSwitch`,extends:S,props:{trueValue:{type:null,default:!0},falseValue:{type:null,default:!1},readonly:{type:Boolean,default:!1},tabindex:{type:Number,default:null},inputId:{type:String,default:null},inputClass:{type:[String,Object],default:null},inputStyle:{type:Object,default:null},ariaLabelledby:{type:String,default:null},ariaLabel:{type:String,default:null}},style:j,provide:function(){return{$pcToggleSwitch:this,$parentInstance:this}}},inheritAttrs:!1,emits:[`change`,`focus`,`blur`],methods:{getPTOptions:function(e){return(e===`root`?this.ptmi:this.ptm)(e,{context:{checked:this.checked,disabled:this.disabled}})},onChange:function(e){if(!this.disabled&&!this.readonly){var t=this.checked?this.falseValue:this.trueValue;this.writeValue(t,e),this.$emit(`change`,e)}},onFocus:function(e){this.$emit(`focus`,e)},onBlur:function(e){var t,n;this.$emit(`blur`,e),(t=(n=this.formField).onBlur)==null||t.call(n,e)}},computed:{checked:function(){return this.d_value===this.trueValue},dataP:function(){return b({checked:this.checked,disabled:this.disabled,invalid:this.$invalid})}}},N=[`data-p-checked`,`data-p-disabled`,`data-p`],P=[`id`,`checked`,`tabindex`,`disabled`,`readonly`,`aria-checked`,`aria-labelledby`,`aria-label`,`aria-invalid`],F=[`data-p`],I=[`data-p`];function L(e,t,r,a,s,l){return i(),c(`div`,n({class:e.cx(`root`),style:e.sx(`root`)},l.getPTOptions(`root`),{"data-p-checked":l.checked,"data-p-disabled":e.disabled,"data-p":l.dataP}),[O(`input`,n({id:e.inputId,type:`checkbox`,role:`switch`,class:[e.cx(`input`),e.inputClass],style:e.inputStyle,checked:l.checked,tabindex:e.tabindex,disabled:e.disabled,readonly:e.readonly,"aria-checked":l.checked,"aria-labelledby":e.ariaLabelledby,"aria-label":e.ariaLabel,"aria-invalid":e.invalid||void 0,onFocus:t[0]||=function(){return l.onFocus&&l.onFocus.apply(l,arguments)},onBlur:t[1]||=function(){return l.onBlur&&l.onBlur.apply(l,arguments)},onChange:t[2]||=function(){return l.onChange&&l.onChange.apply(l,arguments)}},l.getPTOptions(`input`)),null,16,P),O(`div`,n({class:e.cx(`slider`)},l.getPTOptions(`slider`),{"data-p":l.dataP}),[O(`div`,n({class:e.cx(`handle`)},l.getPTOptions(`handle`),{"data-p":l.dataP}),[o(e.$slots,`handle`,{checked:l.checked})],16,I)],16,F)],16,N)}M.render=L;var R=`hiddenFileSettings`,z=[`*.stories.*`,`*_spec.rb`,`*.spec.ts`];function B(e){if(typeof e!=`object`||!e)return{enabled:!1,patterns:[...z]};let t=`enabled`in e&&e.enabled===!0,n=`patterns`in e?e.patterns:null;return{enabled:t,patterns:Array.isArray(n)&&n.every(e=>typeof e==`string`)?n:[...z]}}async function V(){return B((await chrome.storage.sync.get(R))[R])}async function H(e,t){let n=t?{...e,source:t}:e;await chrome.storage.sync.set({[R]:n})}function U(e,t){function n(n,r){if(r===`sync`&&`hiddenFileSettings`in n){let r=n[R]?.newValue;W(r,t)||e(B(r))}}return chrome.storage.onChanged.addListener(n),()=>chrome.storage.onChanged.removeListener(n)}function W(e,t){return t!==void 0&&typeof e==`object`&&!!e&&`source`in e&&e.source===t}function G(e,n){let i=r(``),a=r(null),o=T(()=>a.value?k(a.value):``),s=T(()=>a.value!==null);function c(){a.value=null}function l(){let t=i.value.trim();a.value=K(t,e.value),!a.value&&(n(t),i.value=``)}return t(i,c),{pattern:i,feedback:o,invalid:s,handleAdd:l}}function K(e,t){return e?t.includes(e)?`filePatternDuplicate`:null:`filePatternEmpty`}function q(){let e=r({enabled:!1,patterns:[]}),t=r(!1),n=r(!1),i=T(()=>!t.value),a=crypto.randomUUID(),o=Y(e,n,a);function s(t){o({...e.value,enabled:t})}function c(t){o({...e.value,patterns:[...e.value.patterns,t]})}function l(t){o({...e.value,patterns:e.value.patterns.filter(e=>e!==t)})}J({settings:e,ready:t,failed:n,source:a});let u=T(()=>e.value.enabled),d=T(()=>e.value.patterns);return{enabled:u,patterns:d,...G(d,c),disabled:i,failed:n,handleToggle:s,remove:l}}function J(t){let n;e(async()=>{try{t.settings.value=await V(),n=U(e=>{t.settings.value=e},t.source),t.ready.value=!0}catch{t.failed.value=!0}}),v(()=>n?.())}function Y(e,t,n){let r=Promise.resolve();return i=>{e.value=i,t.value=!1,r=r.then(()=>H(i,n)).catch(()=>{t.value=!0})}}var X={class:`settings-pattern`},Z=l({__name:`PatternItem`,props:{pattern:{},disabled:{type:Boolean}},emits:[`remove`],setup(e,{emit:t}){let n=e,r=t,a=T(()=>k(`removePattern`,{pattern:n.pattern}));function o(){r(`remove`,n.pattern)}return(t,n)=>(i(),c(`li`,X,[O(`code`,null,s(e.pattern),1),E(d(_),{type:`button`,icon:`pi pi-trash`,severity:`secondary`,size:`small`,text:``,rounded:``,"aria-label":a.value,disabled:e.disabled,onClick:o},null,8,[`aria-label`,`disabled`])]))}}),ee={id:`file-settings`},Q={class:`settings-header flex items-center gap-3`},$=[`alt`],te={class:`settings-card`,"aria-labelledby":`hide-files-title`},ne={id:`hide-files-title`},re={class:`settings-help`},ie=[`disabled`],ae={class:`settings-toggle flex items-center justify-between gap-3`},oe={for:`hide-matching-files`},se={id:`patterns-label`},ce={"aria-labelledby":`patterns-label`},le={for:`file-pattern`},ue={class:`settings-add-row flex gap-2`},de={id:`pattern-feedback`,class:`settings-feedback`,"aria-live":`polite`},fe={key:0,class:`settings-error`,role:`alert`},pe=`./icons/icon-48.png`,me=l({__name:`App`,setup(e){let{enabled:t,patterns:n,pattern:r,disabled:o,failed:l,feedback:m,invalid:v,handleToggle:y,handleAdd:b,remove:S}=q(),C=k(`settingsPageTitle`),w=k(`hideFilesTitle`),A=k(`hideFilesEnabled`),j=k(`hideFilesHelp`),N=k(`filePatternsLabel`),P=k(`filePattern`),F=k(`filePatternPlaceholder`),I=k(`addPattern`),L=k(`fileSettingsError`),R=k(`extName`),z=T(()=>({"patterns-inactive":!t.value}));function B(e){S(e)}return(e,S)=>(i(),c(`main`,ee,[O(`header`,Q,[O(`img`,{src:pe,alt:d(R),class:`settings-icon`},null,8,$),O(`h1`,null,s(d(C)),1)]),O(`section`,te,[O(`h2`,ne,s(d(w)),1),O(`p`,re,s(d(j)),1),O(`fieldset`,{disabled:d(o)},[O(`div`,ae,[O(`label`,oe,s(d(A)),1),E(d(M),{"input-id":`hide-matching-files`,"model-value":d(t),disabled:d(o),"onUpdate:modelValue":d(y)},null,8,[`model-value`,`disabled`,`onUpdate:modelValue`])]),O(`div`,{class:f([`settings-patterns`,z.value])},[O(`h3`,se,s(d(N)),1),O(`ul`,ce,[(i(!0),c(h,null,a(d(n),e=>(i(),p(Z,{key:e,pattern:e,disabled:d(o),onRemove:B},null,8,[`pattern`,`disabled`]))),128))])],2),O(`form`,{onSubmit:S[1]||=g((...e)=>d(b)&&d(b)(...e),[`prevent`]),class:`settings-add-form`},[O(`label`,le,s(d(P)),1),O(`div`,ue,[E(d(x),{id:`file-pattern`,modelValue:d(r),"onUpdate:modelValue":S[0]||=e=>u(r)?r.value=e:null,placeholder:d(F),disabled:d(o),invalid:d(v),"aria-describedby":`pattern-feedback`},null,8,[`modelValue`,`placeholder`,`disabled`,`invalid`]),E(d(_),{type:`submit`,size:`small`,icon:`pi pi-plus`,label:d(I),disabled:d(o)},null,8,[`label`,`disabled`])]),O(`p`,de,s(d(m)),1)],32)],8,ie),d(l)?(i(),c(`p`,fe,s(d(L)),1)):D(``,!0)])]))}});document.title=k(`settingsPageTitle`),document.documentElement.lang=A(),m(me).use(y,C).mount(`#app`);