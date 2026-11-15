'use strict';
(function(root){function appendRecovery(current,message){if(!message||current===message)return current;return current?current+'\n\n'+message:message;}const api={appendRecovery};if(typeof module==='object')module.exports=api;else root.LibraryState=api;})(globalThis);
