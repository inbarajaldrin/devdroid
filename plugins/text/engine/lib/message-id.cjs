// Engine IDs carry a reserved tag plus 64 bits of the SDK-generated suffix.
// This is an engine format, distinct from the pinned SDK's 22-character IDs.
const prefix='3EB0D3D0D3D0D3D0';
function allocate(native){if(!/^(?:3EB0[A-F0-9]{18}|[A-F0-9]{32})$/i.test(native)){const error=new (require('./errors.cjs').Refusal)('MESSAGE_ID','Invalid SDK message identifier');error.notDelivered=true;throw error;}return prefix+native.slice(-16).toUpperCase();}
const ours=id=>/^3EB0D3D0D3D0D3D0[A-F0-9]{16}A?$/.test(id || '');
module.exports={allocate,ours};
