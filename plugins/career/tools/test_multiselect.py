"""Multi-select facts use the same drafting, Questions, personal review and digest as text facts."""
import pytest
import yaml

from runtime import Refused, read_json
from test_workflow import api, system

NAME='Which offices are you willing to work in?'
OPTIONS=['Riverton, ZZ','Northport, ZZ','Eastfield, ZZ']


def add_group(system):
    system.browser.fields.append({'name':NAME,'type':'multiselect','required':True,'value':'','options':OPTIONS})


def test_unknown_multi_fact_goes_to_questions_then_exact_ticks_are_reviewed(system,api):
    add_group(system)
    system.dispatch('pick','job-one',decision='approved')
    with pytest.raises(Refused) as error:system.dispatch('fill','job-one')
    assert error.value.code=='MISSING_FACT'
    assert api('POST','/postings/job-one/review',{'name':NAME,'decision':'edit','value':'Eastfield, ZZ; Northport, ZZ','by':'web'})[0]==200
    assert next(box for box in system.review('job-one')['boxes'] if box['name']==NAME)['value']=='Northport, ZZ; Eastfield, ZZ'
    system.dispatch('apply-edits','job-one')
    assert next(field for field in system.browser.fields if field['name']==NAME)['value']=='Northport, ZZ; Eastfield, ZZ'
    before=system.review('job-one')['fill_digest']
    assert api('POST','/postings/job-one/review',{'name':NAME,'decision':'edit','value':'Riverton, ZZ','by':'phone'})[0]==200
    system.dispatch('apply-edits','job-one')
    assert system.review('job-one')['fill_digest']!=before
    assert api('POST','/postings/job-one/review',{'name':NAME,'decision':'edit','value':'unknown','by':'phone'})[0]==409


def test_profile_multiselect_lists_are_exactly_normalized_and_reused(system):
    add_group(system)
    profile=yaml.safe_load((system.root/'profile.yml').read_text())
    profile['standard_answers'][NAME]=['Eastfield, ZZ','Riverton, ZZ']
    (system.root/'profile.yml').write_text(yaml.safe_dump(profile))
    system.dispatch('pick','job-one',decision='approved')
    review=system.dispatch('fill','job-one')
    assert next(box for box in review['boxes'] if box['name']==NAME)['value']=='Riverton, ZZ; Eastfield, ZZ'
    calls=len(system.agents.tasks)
    system.dispatch('fill','job-one')
    assert len(system.agents.tasks)==calls
