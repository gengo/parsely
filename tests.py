import json
import os
import pkg_resources
import unittest.mock

import celery
import falcon
import pytest

import brokkoly

# We don't need actual celery for testing.
celery.Celery = unittest.mock.MagicMock()


def task_for_test(text: str, number: int):
    pass


class TestBrokkoly:
    def setup_method(self, method):
        self.brokkoly = brokkoly.Brokkoly('test_queue', 'test_broker')

    def teardown_method(self, method):
        brokkoly._tasks.clear()

    def test_task(self):
        self.brokkoly.task()(task_for_test)

        (processor, validations), preprocessors = self.brokkoly._tasks['task_for_test']
        assert len(preprocessors) == 0

        for validation, expect in zip(
                sorted(validations, key=lambda x: x[0]),
                [('number', int), ('text', str)]
        ):
            assert validation == expect

    def test_register_same_task(self):
        self.brokkoly.task()(task_for_test)

        with pytest.raises(brokkoly.BrokkolyError):
            self.brokkoly.task()(task_for_test)

    def test_queue_name_startw_with__(self):
        with pytest.raises(brokkoly.BrokkolyError):
            brokkoly.Brokkoly('_queue', 'test_broker')


class TestProducer:
    def setup_method(self, method):
        self.brokkoly = brokkoly.Brokkoly('test_queue', 'test_broker')
        self.brokkoly.task()(task_for_test)
        self.producer = brokkoly.Producer()
        self.mock_req = unittest.mock.MagicMock()
        self.mock_resp = unittest.mock.MagicMock()

    def teardown_method(self, method):
        brokkoly._tasks.clear()

    def test_undefined_queue(self):
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(
                self.mock_req, self.mock_resp, 'undefined_queue', 'undefined_task')

        assert e.value.title == "Undefined queue"

    def test_undefined_task(self):
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(self.mock_req, self.mock_resp, 'test_queue', 'undefined_task')

        assert e.value.title == "Undefined task"

    def test_empty_payload(self):
        self.mock_req.stream.read.return_value = b""
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(self.mock_req, self.mock_resp, 'test_queue', 'task_for_test')

        assert e.value.title == "Empty payload"

    def test_non_json_payload(self):
        self.mock_req.stream.read.return_value = b"This is not a JSON"
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(self.mock_req, self.mock_resp, 'test_queue', 'task_for_test')

        assert e.value.title == "Payload is not a JSON"

    def test_lack_message(self):
        self.mock_req.stream.read.return_value = b"{}"
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(self.mock_req, self.mock_resp, 'test_queue', 'task_for_test')

        assert e.value.title == "Invalid JSON"

    def test_preprocessor_lack_requirements(self):
        def preprocessor_for_preprocessor_test(text: str):
            pass

        @self.brokkoly.task(preprocessor_for_preprocessor_test)
        def task_for_preprocessor_test():
            pass

        self.mock_req.stream.read.return_value = json.dumps({
            'message': {}
        }).encode()
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(
                self.mock_req, self.mock_resp, 'test_queue', 'task_for_preprocessor_test')

        assert e.value.title == "Missing required filed"

    def test_preprocessor_invalid_type(self):
        def preprocessor_for_preprocessor_test(text: str):
            pass

        @self.brokkoly.task(preprocessor_for_preprocessor_test)
        def task_for_preprocessor_test():
            pass

        self.mock_req.stream.read.return_value = json.dumps({
            'message': {
                'text': 1
            }
        }).encode()
        with pytest.raises(falcon.HTTPBadRequest) as e:
            self.producer.on_post(
                self.mock_req, self.mock_resp, 'test_queue', 'task_for_preprocessor_test')

        assert e.value.title == "Invalid type"

    def test_task(self):
        def preprocessor_for_preprocessor_test(number: int):
            return {
                'text': str(number)
            }

        @self.brokkoly.task(preprocessor_for_preprocessor_test)
        def task_for_preprocessor_test(text: str):
            pass

        self.mock_req.stream.read.return_value = json.dumps({
            'message': {
                'number': 1
            }
        }).encode()

        self.producer.on_post(
            self.mock_req, self.mock_resp, 'test_queue', 'task_for_preprocessor_test')

        assert self.brokkoly._tasks['task_for_preprocessor_test'][0][0].apply_async.called

    def test_on_get(self):
        self.producer.on_get(self.mock_req, self.mock_resp, 'test_queue', 'task_for_test')
        self.mock_resp.content_type = 'text/html'


class TestStaticResource:
    @unittest.mock.patch.object(pkg_resources, "WorkingSet")
    def test_installed(self, mock_WorkingSet):
        mock_info = unittest.mock.MagicMock(project_name='brokkoly', location='/path/to/lib')
        mock_WorkingSet.return_value = [mock_info]

        assert brokkoly.StaticResource().is_packaged

    @unittest.mock.patch.object(pkg_resources, "WorkingSet")
    def test_not_installed(self, mock_WorkingSet):
        mock_WorkingSet.return_value = []
        assert not brokkoly.StaticResource().is_packaged

    @unittest.mock.patch.object(pkg_resources, "resource_filename")
    def test_on_get_for_installed(self, mock_resource_filename):
        resourcename = "brokkoly.js"
        mock_resource_filename.return_value = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "brokkoly", "resources", resourcename
        )

        static_resource = brokkoly.StaticResource()
        static_resource.is_packaged = True

        mock_resp = unittest.mock.MagicMock()
        static_resource.on_get(unittest.mock.MagicMock(), mock_resp, resourcename)
        mock_resp.content_type = "application/javascript"

    def test_on_get_for_not_installed(self):
        static_resource = brokkoly.StaticResource()
        static_resource.is_packaged = False

        mock_resp = unittest.mock.MagicMock()
        static_resource.on_get(unittest.mock.MagicMock(), mock_resp, "brokkoly.js")
        mock_resp.content_type = "application/javascript"


def test_producer():
    assert isinstance(brokkoly.producer(), falcon.api.API)
