Feature: Service health
  Scenario: Operator checks the backend is up
    When the operator requests the health endpoint
    Then the service reports it is ok
