Feature: Coverage

    @auth
    Scenario: Empty coverage list
        Given empty "coverage"
        When we get "/coverage"
        Then we get list with 0 items

    @auth
    @notification
    Scenario: Create new coverage item
        Given empty "users"
        Given empty "coverage"
        Given empty "planning"
        When we post to "planning"
        """
        [{
            "slugline": "planning 1"
        }]
        """
        When we post to "users"
        """
        {"username": "foo", "email": "foo@bar.com", "is_active": true, "sign_off": "abc"}
        """
        Then we get existing resource
        """
        {"_id": "#users._id#", "invisible_stages": []}
        """
        When we post to "/coverage" with success
        """
        [
            {
                "guid": "123",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }
        ]
        """
        When we get "/coverage"
        Then we get list with 1 items
        """
            {"_items": [{
                "guid": "__any_value__",
                "original_creator": "__any_value__",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "delivery": []
            }]}
        """
        When we get "/coverage_history"
        Then we get a list with 1 items
        """
            {"_items": [{"operation": "create", "coverage_id": "#coverage._id#", "update": {
                "planning_item": "#planning._id#"
            }}]}
        """

    @auth
    @notification
    Scenario: Coverage assignment audit information is populated.
        Given empty "users"
        Given empty "coverage"
        Given empty "planning"
        When we post to "planning"
        """
        [{
            "slugline": "planning 1"
        }]
        """
        When we post to "users"
        """
        {"username": "foo", "email": "foo@bar.com", "is_active": true, "sign_off": "abc"}
        """
        Then we get existing resource
        """
        {"_id": "#users._id#", "invisible_stages": []}
        """
        When we post to "/coverage" with success
        """
        [
            {
                "guid": "123",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "delivery": [],
                "planning_item": "#planning._id#"
            }
        ]
        """
        When we get "/coverage"
        Then we get list with 1 items
        """
            {"_items": [{
                "guid": "__any_value__",
                "original_creator": "__any_value__",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea",
                        "assigned_by": "#CONTEXT_USER_ID#",
                        "assigned_date": "__any_value__"
                    }
                },
                "delivery": []
            }]}
        """

    @auth
    @notification
    Scenario: Sends notification on coverage changes
        Given empty "users"
        Given empty "coverage"
        Given empty "planning"
        When we post to "users"
        """
        {"username": "foo", "email": "foo@bar.com", "is_active": true, "sign_off": "abc"}
        """
        And we reset notifications
        Then we get existing resource
        """
        {"_id": "#users._id#", "invisible_stages": []}
        """
        When we post to "planning"
        """
        [{
            "slugline": "planning 1"
        }]
        """
        Then we store "planningId" with value "#planning._id#" to context
        When we post to "/coverage"
        """
        [
            {
                "guid": "123",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "delivery": [],
                "planning_item": "#planningId#"
            }
        ]
        """
        Then we get OK response
        And we get notifications
        """
        [{
            "event": "coverage:created",
            "extra": {
                "item": "#coverage._id#",
                "planning": "#planningId#",
                "user": "#CONTEXT_USER_ID#"
            }
        }]
        """
        When we reset notifications
        And we patch "/coverage/#coverage._id#"
        """
        {
            "planning": {
                "ednote": "testing changes",
                "assigned_to": {
                    "desk": "Politic Desk",
                    "user": "c507f191e810c19729de860e"
                }
            }
        }
        """
        Then we get OK response
        And we get notifications
        """
        [{
            "event": "coverage:updated",
            "extra": {
                "item": "#coverage._id#",
                "planning": "#planningId#",
                "user": "#CONTEXT_USER_ID#"
            }
        }]
        """
        When we reset notifications
        And we delete "/coverage/#coverage._id#"
        Then we get OK response
        And we get notifications
        """
        [{
            "event": "coverage:deleted",
            "extra": {
                "item": "#coverage._id#",
                "planning": "#planningId#",
                "user": "#CONTEXT_USER_ID#"
            }
        }]
        """

    @auth
    @notification
    Scenario: Coverage history tracks updates
        Given empty "coverage"
        Given empty "planning"
        When we post to "planning"
        """
        [{
            "slugline": "planning 1"
        }]
        """
        Then we store "planningId" with value "#planning._id#" to context
        When we post to "/coverage" with success
        """
        [
             {
                "guid": "123",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "delivery": [],
                "planning_item": "#planning._id#"
            }
        ]
        """
        Then we get OK response
        When we patch "/coverage/#coverage._id#"
        """
        {"planning": {
            "ednote": "test coverage, I want 251 words",
            "assigned_to": {
                "desk": "Politic Desk",
                "user": "507f191e810c19729de860ea"
            }
        }}
        """
        Then we get OK response
        When we get "/coverage_history"
        Then we get a list with 2 items
        """
            {"_items": [{
                "coverage_id":  "#coverage._id#",
                "operation": "create",
                "update": {
                    "planning": {"assigned_to": { "desk": "Politic Desk", "user": "507f191e810c19729de860ea" }}
                    }},
                {"coverage_id":  "#coverage._id#",
                "operation": "update",
                "update": {"planning": {
                    "ednote": "test coverage, I want 251 words",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                }}}
            ]}
        """
        When we get "/coverage_history?where=coverage_id==%22#coverage._id#%22"
        Then we get list with 2 items
        """
            {"_items": [{
                "coverage_id":  "#coverage._id#",
                "operation": "create"
                },
                {"coverage_id":  "#coverage._id#",
                "operation": "update"
                }
            ]}
        """
        When we get "/planning_history?where=planning_id==%22#planning._id#%22"
        Then we get list with 3 items
        """
            {"_items": [
                {"operation": "create"},
                {"operation": "coverage created",
                    "update": {"coverage_id": "#coverage._id#"}},
                {"operation": "coverage updated",
                    "update": {"coverage_id": "#coverage._id#"}}
            ]}
        """
        When we delete "/coverage/#coverage._id#"
        When we get "/planning_history?where=planning_id==%22#planning._id#%22"
        Then we get list with 4 items
        """
            {"_items": [
                {"operation": "create"},
                {"operation": "coverage created",
                    "update": {"coverage_id": "#coverage._id#"}},
                {"operation": "coverage updated",
                    "update": {"coverage_id": "#coverage._id#"}},
                {"operation": "coverage deleted",
                    "update": {"coverage_id": "#coverage._id#"}}
            ]}
        """
    @auth
    @notification
    Scenario: Create or update coverage - sync coverage information to planning
        Given empty "users"
        Given empty "coverage"
        Given empty "planning"
        When we post to "planning"
        """
        [{
            "slugline": "planning 1"
        }]
        """
        Then we get OK response
        Then we store "planning_date" with value "#planning._planning_date#" to context
        When we post to "/coverage"
        """
        [
            {
                "guid": "123",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "text",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }
        ]
        """
        Then we get OK response
        Then we store "coverage1" with value "#coverage._id#" to context
        When we get "/coverage"
        Then we get list with 1 items
        """
            {"_items": [{
                "guid": "__any_value__",
                "original_creator": "__any_value__",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "text",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    }
                },
                "delivery": []
            }]}
        """
        When we get "/planning/#planning._id#"
        Then we get existing resource
        """
        {
            "slugline": "planning 1",
            "coverages": [{
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "text",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea",
                        "assigned_by": "#CONTEXT_USER_ID#",
                        "assigned_date": "__any_value__"
                    }
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }],
            "_coverages": [
                {
                    "coverage_id" : null,
                    "scheduled" : "__any_value__",
                    "g2_content_type": null
                },
                {
                    "coverage_id" : "#coverage._id#",
                    "scheduled" : null,
                    "g2_content_type": "text"
                }
            ]
        }
        """
        When we patch "/coverage/#coverage._id#"
        """
        {"planning": { "scheduled": "#DATE+1#", "g2_content_type": "text" }}
        """
        Then we get updated response
        When we get "/planning/#planning._id#"
        Then we get existing resource
        """
        {
            "slugline": "planning 1",
            "coverages": [{
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "text",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea",
                        "assigned_by": "#CONTEXT_USER_ID#",
                        "assigned_date": "__any_value__"
                    }
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }],
            "_coverages": [
                {
                    "coverage_id": "#coverage._id#",
                    "scheduled": "__any_value__",
                    "g2_content_type": "text"
                }
            ]
        }
        """
        When we post to "/coverage"
        """
        [
            {
                "guid": "456",
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "video",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea"
                    },
                    "scheduled": "#DATE+3#"
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }
        ]
        """
        Then we get OK response
        Then we store "coverage2" with value "#coverage._id#" to context
        When we get "/planning/#planning._id#"
        Then we get existing resource
        """
        {
            "slugline": "planning 1",
            "coverages": [{
                "planning": {
                    "ednote": "test coverage, I want 250 words",
                    "g2_content_type": "text",
                    "assigned_to": {
                        "desk": "Politic Desk",
                        "user": "507f191e810c19729de860ea",
                        "assigned_by": "#CONTEXT_USER_ID#",
                        "assigned_date": "__any_value__"
                    }
                },
                "planning_item": "#planning._id#",
                "delivery": []
            }],
            "_coverages": [
                {
                    "coverage_id": "#coverage1#",
                    "scheduled": "__any_value__",
                    "g2_content_type": "text"
                },
                {
                    "coverage_id": "#coverage2#",
                    "scheduled": "__any_value__",
                    "g2_content_type": "video"
                }
            ]
        }
        """