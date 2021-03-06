import React from 'react'
import PropTypes from 'prop-types'
import { isBoolean, get } from 'lodash'
import { Checkbox } from '../index'
import './style.scss'

export const CheckboxField = ({
    input,
    label,
    readOnly,
    labelPosition,
    type,
    currentValue,
    meta: { touched, error, warning },
}) => {
    const isRadio = type === 'radio'
    input = {
        ...input,
        type: type,
        value: isRadio ? get(input, 'value') :
            isBoolean(get(input, 'value')) ? get(input, 'value') : false,
    }
    return (
        <label>
            <Checkbox
                {...input}
                labelPosition={labelPosition}
                label={label}
                checkedValue={currentValue}
                onChange={
                    () => {
                        input.onChange( isRadio ? input.value : !input.value)
                    }
                }
                readOnly={readOnly}/>
            {touched && ((error && <span className="error-block">{error}</span>) ||
                (warning && <span className="help-block">{warning}</span>))}
        </label>
    )
}

CheckboxField.propTypes = {
    input: PropTypes.object.isRequired,
    label: PropTypes.string,
    currentValue: PropTypes.string,
    labelPosition: PropTypes.oneOf(['left', 'right', 'inside']),
    meta: PropTypes.object,
    readOnly: PropTypes.bool,
    type: PropTypes.oneOf(['radio', 'checkbox']),
}

CheckboxField.defaultProps = { type: 'checkbox' }