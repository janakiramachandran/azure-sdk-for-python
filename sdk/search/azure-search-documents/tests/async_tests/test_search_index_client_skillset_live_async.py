# -------------------------------------------------------------------------
# Copyright (c) Microsoft Corporation. All rights reserved.
# Licensed under the MIT License. See License.txt in the project root for
# license information.
# --------------------------------------------------------------------------
import asyncio
import functools
import json
from os.path import dirname, join, realpath
import time

import pytest
from azure.core import MatchConditions
from azure.core.credentials import AzureKeyCredential
from devtools_testutils import AzureMgmtTestCase

from azure_devtools.scenario_tests import ReplayableTest
from azure_devtools.scenario_tests.utilities import trim_kwargs_from_test_function

from search_service_preparer import SearchServicePreparer, SearchResourceGroupPreparer

from azure.core.exceptions import HttpResponseError
from azure.search.documents.indexes.models import(
    EntityLinkingSkill,
    EntityRecognitionSkill,
    EntityRecognitionSkillVersion,
    InputFieldMappingEntry,
    OutputFieldMappingEntry,
    SearchIndexerSkillset,
    SentimentSkill,
    SentimentSkillVersion
)
from azure.search.documents.indexes.aio import SearchIndexerClient

CWD = dirname(realpath(__file__))
SCHEMA = open(join(CWD, "..", "hotel_schema.json")).read()
BATCH = json.load(open(join(CWD, "..", "hotel_small.json"), encoding='utf-8'))
TIME_TO_SLEEP = 5

def await_prepared_test(test_fn):
    """Synchronous wrapper for async test methods. Used to avoid making changes
    upstream to AbstractPreparer (which doesn't await the functions it wraps)
    """

    @functools.wraps(test_fn)
    def run(test_class_instance, *args, **kwargs):
        trim_kwargs_from_test_function(test_fn, kwargs)
        loop = asyncio.get_event_loop()
        return loop.run_until_complete(test_fn(test_class_instance, **kwargs))

    return run

class SearchSkillsetClientTest(AzureMgmtTestCase):
    FILTER_HEADERS = ReplayableTest.FILTER_HEADERS + ['api-key']

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_create_skillset(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        name = "test-ss"

        s1 = EntityRecognitionSkill(name="skill1",
                                    inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                    outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizationsS1")],
                                    description="Skill Version 1",
                                    model_version="1",
                                    include_typeless_entities=True)

        s2 = EntityRecognitionSkill(name="skill2",
                                    inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                    outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizationsS2")],
                                    skill_version=EntityRecognitionSkillVersion.LATEST,
                                    description="Skill Version 3",
                                    model_version="3",
                                    include_typeless_entities=True)
        s3 = SentimentSkill(name="skill3",
                            inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                            outputs=[OutputFieldMappingEntry(name="score", target_name="scoreS3")],
                            skill_version=SentimentSkillVersion.V1,
                            description="Sentiment V1",
                            include_opinion_mining=True)

        s4 = SentimentSkill(name="skill4",
                            inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                            outputs=[OutputFieldMappingEntry(name="confidenceScores", target_name="scoreS4")],
                            skill_version=SentimentSkillVersion.V3,
                            description="Sentiment V3",
                            include_opinion_mining=True)

        s5 = EntityLinkingSkill(name="skill5",
                                inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                outputs=[OutputFieldMappingEntry(name="entities", target_name="entitiesS5")],
                                minimum_precision=0.5)

        skillset = SearchIndexerSkillset(name=name, skills=list([s1, s2, s3, s4, s5]), description="desc")
        result = await client.create_skillset(skillset)

        assert isinstance(result, SearchIndexerSkillset)
        assert result.name == "test-ss"
        assert result.description == "desc"
        assert result.e_tag
        assert len(result.skills) == 5
        assert isinstance(result.skills[0], EntityRecognitionSkill)
        assert result.skills[0].skill_version == EntityRecognitionSkillVersion.V1
        assert isinstance(result.skills[1], EntityRecognitionSkill)
        assert result.skills[1].skill_version == EntityRecognitionSkillVersion.V3
        assert isinstance(result.skills[2], SentimentSkill)
        assert result.skills[2].skill_version == SentimentSkillVersion.V1
        assert isinstance(result.skills[3], SentimentSkill)
        assert result.skills[3].skill_version == SentimentSkillVersion.V3
        assert isinstance(result.skills[4], EntityLinkingSkill)
        assert result.skills[4].minimum_precision == 0.5

        assert len(await client.get_skillsets()) == 1

        await client.reset_skills(result, [x.name for x in result.skills])

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_delete_skillset(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc")
        result = await client.create_skillset(skillset)
        assert len(await client.get_skillsets()) == 1

        await client.delete_skillset("test-ss")
        if self.is_live:
            time.sleep(TIME_TO_SLEEP)
        assert len(await client.get_skillsets()) == 0

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_delete_skillset_if_unchanged(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc")
        result = await client.create_skillset(skillset)
        etag = result.e_tag

        skillset1 = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="updated")
        updated = await client.create_or_update_skillset(skillset1)
        updated.e_tag = etag

        with pytest.raises(HttpResponseError):
            await client.delete_skillset(updated, match_condition=MatchConditions.IfNotModified)

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_get_skillset(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc")
        await client.create_skillset(skillset)
        assert len(await client.get_skillsets()) == 1

        result = await client.get_skillset("test-ss")
        assert isinstance(result, SearchIndexerSkillset)
        assert result.name == "test-ss"
        assert result.description == "desc"
        assert result.e_tag
        assert len(result.skills) == 1
        assert isinstance(result.skills[0], EntityRecognitionSkill)

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_get_skillsets(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset1 = SearchIndexerSkillset(name='test-ss-1', skills=list([s]), description="desc1")
        await client.create_skillset(skillset1)
        skillset2 = SearchIndexerSkillset(name='test-ss-2', skills=list([s]), description="desc2")
        await client.create_skillset(skillset2)
        result = await client.get_skillsets()
        assert isinstance(result, list)
        assert all(isinstance(x, SearchIndexerSkillset) for x in result)
        assert set(x.name for x in result) == {"test-ss-1", "test-ss-2"}

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_create_or_update_skillset(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset1 = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc1")
        await client.create_or_update_skillset(skillset1)
        skillset2 = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc2")
        await client.create_or_update_skillset(skillset2)
        assert len(await client.get_skillsets()) == 1

        result = await client.get_skillset("test-ss")
        assert isinstance(result, SearchIndexerSkillset)
        assert result.name == "test-ss"
        assert result.description == "desc2"

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_create_or_update_skillset_inplace(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset1 = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc1")
        ss = await client.create_or_update_skillset(skillset1)
        skillset2 = SearchIndexerSkillset(name='test-ss', skills=[s], description="desc2", skillset=ss)
        await client.create_or_update_skillset(skillset2)
        assert len(await client.get_skillsets()) == 1

        result = await client.get_skillset("test-ss")
        assert isinstance(result, SearchIndexerSkillset)
        assert result.name == "test-ss"
        assert result.description == "desc2"

    @SearchResourceGroupPreparer(random_name_enabled=True)
    @SearchServicePreparer(schema=SCHEMA, index_batch=BATCH)
    async def test_create_or_update_skillset_if_unchanged(self, api_key, endpoint, index_name, **kwargs):
        client = SearchIndexerClient(endpoint, AzureKeyCredential(api_key))
        s = EntityRecognitionSkill(inputs=[InputFieldMappingEntry(name="text", source="/document/content")],
                                   outputs=[OutputFieldMappingEntry(name="organizations", target_name="organizations")])

        skillset1 = SearchIndexerSkillset(name='test-ss', skills=list([s]), description="desc1")
        ss = await client.create_or_update_skillset(skillset1)
        etag = ss.e_tag

        skillset2 = SearchIndexerSkillset(name='test-ss', skills=[s], description="desc2", skillset=ss)
        await client.create_or_update_skillset(skillset2)
        assert len(await client.get_skillsets()) == 1
